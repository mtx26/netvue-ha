package manager

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/mpeg4audio"
	"github.com/pion/opus"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
)

type testRTSPHandler struct{ stream *gortsplib.ServerStream }

func (h *testRTSPHandler) OnDescribe(*gortsplib.ServerHandlerOnDescribeCtx) (*base.Response, *gortsplib.ServerStream, error) {
	return &base.Response{StatusCode: base.StatusOK}, h.stream, nil
}
func (h *testRTSPHandler) OnSetup(*gortsplib.ServerHandlerOnSetupCtx) (*base.Response, *gortsplib.ServerStream, error) {
	return &base.Response{StatusCode: base.StatusOK}, h.stream, nil
}
func (h *testRTSPHandler) OnPlay(*gortsplib.ServerHandlerOnPlayCtx) (*base.Response, error) {
	return &base.Response{StatusCode: base.StatusOK}, nil
}

func requestWebRTC(t *testing.T, handler http.Handler, method, path string, body any) *httptest.ResponseRecorder {
	t.Helper()
	var data []byte
	if body != nil {
		var err error
		data, err = json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
	}
	req := httptest.NewRequest(method, path, bytes.NewReader(data))
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, req)
	return response
}

func TestWebRTCEndToEndSynthetic(t *testing.T) {
	ffmpeg := os.Getenv("NETVUE_TEST_FFMPEG")
	if ffmpeg == "" {
		t.Skip("set NETVUE_TEST_FFMPEG for WebRTC to AAC integration")
	}
	handler := &testRTSPHandler{}
	server := &gortsplib.Server{Handler: handler, RTSPAddress: "127.0.0.1:0"}
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	videoFormat := &format.H264{PayloadTyp: 96, PacketizationMode: 1,
		SPS: []byte{0x67, 0x42, 0xe0, 0x1f, 0xf4, 0x21, 0x32}, PPS: []byte{0x68, 0xc0}}
	videoMedia := &description.Media{Type: description.MediaTypeVideo, Formats: []format.Format{videoFormat}}
	audioFormat := &format.MPEG4Audio{
		PayloadTyp: 97,
		Config: &mpeg4audio.AudioSpecificConfig{
			Type: mpeg4audio.ObjectTypeAACLC, SampleRate: 8000, ChannelConfig: 1,
		},
		SizeLength: 13, IndexLength: 3, IndexDeltaLength: 3,
	}
	audioMedia := &description.Media{Type: description.MediaTypeAudio, Formats: []format.Format{audioFormat}}
	handler.stream = &gortsplib.ServerStream{Server: server, Desc: &description.Session{Medias: []*description.Media{videoMedia, audioMedia}}}
	if err := handler.stream.Initialize(); err != nil {
		t.Fatal(err)
	}
	defer handler.stream.Close()
	audioEncoder, err := audioFormat.CreateEncoder()
	if err != nil {
		t.Fatal(err)
	}
	if err := audioEncoder.Init(); err != nil {
		t.Fatal(err)
	}

	started := make(chan struct{})
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		close(started)
		<-ctx.Done()
		return nil
	})
	manager.source = "127.0.0.1"
	sink := &testTalkSink{first: make(chan struct{}), closeEvents: make(chan struct{}, 4)}
	var audioOpenCount atomic.Int32
	manager.openAudio = func(context.Context, string) (talkSink, error) {
		audioOpenCount.Add(1)
		return sink, nil
	}
	manager.ffmpegPath = func() (string, error) { return ffmpeg, nil }
	host := "192.168.10.20"
	if _, _, err := manager.AddCamera(host, "test"); err != nil {
		t.Fatal(err)
	}
	<-started
	manager.mu.Lock()
	manager.cameras[host].RTSPURL = fmt.Sprintf("rtsp://127.0.0.1:%d/test", server.NetListener().Addr().(*net.TCPAddr).Port)
	manager.mu.Unlock()

	browser := newLocalTestPeerConnection(t)
	defer browser.Close()
	var candidateMu sync.Mutex
	var candidates []webrtc.ICECandidateInit
	browser.OnICECandidate(func(candidate *webrtc.ICECandidate) {
		if candidate != nil {
			candidateMu.Lock()
			candidates = append(candidates, candidate.ToJSON())
			candidateMu.Unlock()
		}
	})
	if _, err := browser.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
		t.Fatal(err)
	}
	audio, err := webrtc.NewTrackLocalStaticSample(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2}, "microphone", "test")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := browser.AddTransceiverFromTrack(audio, webrtc.RTPTransceiverInit{
		Direction: webrtc.RTPTransceiverDirectionSendrecv,
	}); err != nil {
		t.Fatal(err)
	}
	connected := make(chan struct{}, 1)
	browser.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		if state == webrtc.PeerConnectionStateConnected {
			select {
			case connected <- struct{}{}:
			default:
			}
		}
	})
	videoPacketsReceived := make(chan int, 1)
	cameraAudioReceived := make(chan bool, 1)
	videoFirstTimestamp := make(chan uint32, 1)
	cameraAudioFirstTimestamp := make(chan uint32, 1)
	browser.OnTrack(func(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
		if track.Kind() == webrtc.RTPCodecTypeVideo {
			_ = track.SetReadDeadline(time.Now().Add(45 * time.Second))
			count := 0
			for {
				packet, _, err := track.ReadRTP()
				if err != nil {
					videoPacketsReceived <- count
					return
				}
				if count == 0 {
					videoFirstTimestamp <- packet.Timestamp
				}
				count++
			}
		}
		if track.Kind() == webrtc.RTPCodecTypeAudio {
			decoder, err := opus.NewDecoderWithOutput(48000, 2)
			if err != nil {
				cameraAudioReceived <- false
				return
			}
			samples := make([]int16, 5760*2)
			_ = track.SetReadDeadline(time.Now().Add(4 * time.Second))
			for range 100 {
				packet, _, err := track.ReadRTP()
				if err != nil {
					cameraAudioReceived <- false
					return
				}
				select {
				case cameraAudioFirstTimestamp <- packet.Timestamp:
				default:
				}
				count, err := decoder.DecodeToInt16(packet.Payload, samples)
				if err != nil {
					cameraAudioReceived <- false
					return
				}
				for _, sample := range samples[:count*2] {
					if sample != 0 {
						cameraAudioReceived <- true
						return
					}
				}
			}
			cameraAudioReceived <- false
		}
	})
	offer, err := browser.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	sps := videoFormat.SPS
	if err := validateWebRTCSDP(offer.SDP, sps, true, true); err != nil {
		t.Fatalf("compatible offer rejected: %v", err)
	}
	incompatible := strings.ReplaceAll(offer.SDP, "packetization-mode=1", "packetization-mode=0")
	if err := validateWebRTCSDP(incompatible, sps, true, true); err == nil {
		t.Fatal("incompatible packetization accepted")
	}
	gathered := webrtc.GatheringCompletePromise(browser)
	if err := browser.SetLocalDescription(offer); err != nil {
		t.Fatal(err)
	}
	select {
	case <-gathered:
	case <-time.After(5 * time.Second):
		t.Fatal("ICE gathering timed out")
	}
	api := manager.Handler()
	path := "/api/v1/cameras/" + host + "/webrtc/session_1"
	response := requestWebRTC(t, api, http.MethodPut, path, map[string]string{"offer": offer.SDP})
	if response.Code != http.StatusCreated {
		t.Fatalf("PUT: %d %s", response.Code, response.Body)
	}
	candidateMu.Lock()
	if len(candidates) == 0 {
		candidateMu.Unlock()
		t.Fatal("browser produced no ICE candidates")
	}
	for _, candidate := range candidates {
		response = requestWebRTC(t, api, http.MethodPost, path+"/candidates", map[string]any{"candidate": candidate})
		if response.Code != http.StatusNoContent {
			candidateMu.Unlock()
			t.Fatalf("candidate POST: %d %s", response.Code, response.Body)
		}
	}
	candidateMu.Unlock()
	p := manager.webRTCPeer(host, "session_1")
	httpServer := httptest.NewServer(api)
	defer httpServer.Close()
	var after uint64
	deadline := time.Now().Add(8 * time.Second)
	for {
		if time.Now().After(deadline) {
			p.mu.Lock()
			eventSummary := make([]string, 0, len(p.events))
			for _, event := range p.events {
				eventSummary = append(eventSummary, fmt.Sprintf("%d:%s:%s", event.Seq, event.Type, event.Code))
			}
			p.mu.Unlock()
			candidateMu.Lock()
			candidateCount := len(candidates)
			candidateMu.Unlock()
			p.mu.Lock()
			gatheringState := p.pc.ICEGatheringState()
			localDescription := p.pc.LocalDescription()
			serverCandidateCount := 0
			if localDescription != nil {
				for _, line := range strings.Split(localDescription.SDP, "\r\n") {
					if strings.HasPrefix(line, "a=candidate:") {
						serverCandidateCount++
					}
				}
			}
			p.mu.Unlock()
			t.Fatalf("WebRTC did not connect: browser=%s ICE=%s browser_candidates=%d server_candidates=%d gathering=%s events=%v",
				browser.ConnectionState(), browser.ICEConnectionState(),
				candidateCount, serverCandidateCount, gatheringState, eventSummary)
		}
		pollCtx, pollCancel := context.WithTimeout(context.Background(), time.Second)
		req, err := http.NewRequestWithContext(pollCtx, http.MethodGet,
			fmt.Sprintf("%s%s/events?after=%d", httpServer.URL, path, after), nil)
		if err != nil {
			pollCancel()
			t.Fatal(err)
		}
		poll, err := httpServer.Client().Do(req)
		if err != nil {
			pollCancel()
			select {
			case <-connected:
				goto ready
			default:
				continue
			}
		}
		var reply struct {
			Events []webRTCEvent `json:"events"`
		}
		err = json.NewDecoder(poll.Body).Decode(&reply)
		_ = poll.Body.Close()
		pollCancel()
		if err != nil || poll.StatusCode != http.StatusOK {
			t.Fatalf("GET events: status=%d error=%v", poll.StatusCode, err)
		}
		for _, event := range reply.Events {
			if event.Seq != after+1 {
				t.Fatalf("event sequence: %+v", event)
			}
			after = event.Seq
			switch event.Type {
			case "answer":
				if err := browser.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: event.Answer}); err != nil {
					t.Fatal(err)
				}
			case "candidate":
				if err := browser.AddICECandidate(*event.Candidate); err != nil {
					t.Fatal(err)
				}
			case "error":
				t.Fatalf("peer error: %s", event.Code)
			}
		}
		select {
		case <-connected:
			goto ready
		default:
		}
	}
ready:
	videoStop := make(chan struct{})
	videoDone := make(chan struct{})
	var videoStopOnce sync.Once
	stopVideo := func() {
		videoStopOnce.Do(func() { close(videoStop) })
		<-videoDone
	}
	defer stopVideo()
	videoWriteError := make(chan error, 1)
	var videoSequence, videoTimestamp atomic.Uint32
	videoStart := make(chan struct{})
	go func() {
		defer close(videoDone)
		<-videoStart
		ticker := time.NewTicker(20 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-videoStop:
				return
			case <-ticker.C:
				sequence := videoSequence.Add(1) - 1
				timestamp := videoTimestamp.Add(1800) - 1800
				if err := handler.stream.WritePacketRTP(videoMedia, &rtp.Packet{
					Header: rtp.Header{
						Version: 2, PayloadType: 96, SequenceNumber: uint16(sequence),
						Timestamp: timestamp, SSRC: 1, Marker: true,
					},
					Payload: []byte{0x65, 0x80},
				}); err != nil {
					videoWriteError <- err
					return
				}
			}
		}
	}()
	aacFrames := syntheticAACAccessUnits(t, ffmpeg, 2)
	audioPublished := make(chan error, 1)
	go func() {
		<-videoStart
		for _, frame := range aacFrames {
			packets, err := audioEncoder.Encode([][]byte{frame})
			if err != nil {
				audioPublished <- err
				return
			}
			for _, packet := range packets {
				if err := handler.stream.WritePacketRTP(audioMedia, packet); err != nil {
					audioPublished <- err
					return
				}
			}
			time.Sleep(128 * time.Millisecond)
		}
		audioPublished <- nil
	}()
	close(videoStart)
	audioPackets := make([][]byte, 150)
	for i := range audioPackets {
		audioPackets[i] = []byte{0xf8, 0xff, 0xfe}
	}
	fullFFmpeg := os.Getenv("NETVUE_TEST_FFMPEG_FULL")
	if fullFFmpeg != "" {
		audioPackets = syntheticToneOpus(t, fullFFmpeg)
	}
	for _, packet := range audioPackets {
		if err := audio.WriteSample(media.Sample{Data: packet, Duration: 20 * time.Millisecond}); err != nil {
			t.Fatal(err)
		}
		time.Sleep(time.Millisecond)
	}
	select {
	case <-sink.first:
	case <-time.After(5 * time.Second):
		p.mu.Lock()
		events := append([]webRTCEvent(nil), p.events...)
		p.mu.Unlock()
		t.Fatalf("no ADTS from WebRTC audio, events: %+v", events)
	}
	select {
	case audible := <-cameraAudioReceived:
		if !audible {
			t.Fatal("camera AAC audio did not produce decodable non-silent WebRTC Opus")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("no WebRTC audio from RTSP")
	}
	if err := <-audioPublished; err != nil {
		t.Fatalf("publish synthetic RTSP AAC: %v", err)
	}
	select {
	case videoTimestamp := <-videoFirstTimestamp:
		select {
		case audioTimestamp := <-cameraAudioFirstTimestamp:
			videoTimestamp48k := uint32(uint64(videoTimestamp) * 48000 / 90000)
			offset := int32(audioTimestamp - videoTimestamp48k)
			if offset < -24000 || offset > 24000 {
				t.Fatalf("first audio/video timestamps differ by %d samples (>500ms)", offset)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("no first WebRTC camera-audio timestamp")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no first WebRTC video timestamp")
	}
	select {
	case <-sink.closeEvents:
		t.Fatal("talkback closed during a 5-second packet-suppressed silence")
	case <-time.After(5 * time.Second):
	}
	sink.mu.Lock()
	framesBeforeSpeech := len(sink.frames)
	sink.mu.Unlock()
	for i := 0; i < 40; i++ {
		if err := audio.WriteSample(media.Sample{
			Data: audioPackets[i%len(audioPackets)], Duration: 20 * time.Millisecond,
		}); err != nil {
			t.Fatal(err)
		}
	}
	speechDeadline := time.Now().Add(5 * time.Second)
	for {
		sink.mu.Lock()
		received := len(sink.frames) > framesBeforeSpeech
		sink.mu.Unlock()
		if received {
			break
		}
		if time.Now().After(speechDeadline) {
			t.Fatal("talkback did not accept audio after packet-suppressed silence")
		}
		time.Sleep(20 * time.Millisecond)
	}
	if count := audioOpenCount.Load(); count != 1 {
		t.Fatalf("talkback reopened during a short silence: open count %d", count)
	}
	select {
	case <-sink.closeEvents:
	case <-time.After(webRTCAudioIdleTimeout + 5*time.Second):
		t.Fatal("microphone idle did not release the talkback sink")
	}
	sink.mu.Lock()
	framesBeforeResume := len(sink.frames)
	sink.mu.Unlock()
	for i := 0; i < 40; i++ {
		if err := audio.WriteSample(media.Sample{
			Data: audioPackets[i%len(audioPackets)], Duration: 20 * time.Millisecond,
		}); err != nil {
			t.Fatal(err)
		}
	}
	resumeDeadline := time.Now().Add(5 * time.Second)
	for {
		sink.mu.Lock()
		resumed := len(sink.frames) > framesBeforeResume
		sink.mu.Unlock()
		if resumed && audioOpenCount.Load() == 2 {
			break
		}
		if time.Now().After(resumeDeadline) {
			sink.mu.Lock()
			frameCount := len(sink.frames)
			sink.mu.Unlock()
			p.mu.Lock()
			eventSummary := make([]string, 0, len(p.events))
			for _, event := range p.events {
				eventSummary = append(eventSummary, fmt.Sprintf("%d:%s:%s", event.Seq, event.Type, event.Code))
			}
			p.mu.Unlock()
			t.Fatalf("talkback did not resume: opens=%d frames=%d peer_closed=%t events=%v",
				audioOpenCount.Load(), frameCount, p.ctx.Err() != nil, eventSummary)
		}
		time.Sleep(20 * time.Millisecond)
	}
	stopVideo()
	select {
	case err := <-videoWriteError:
		t.Fatalf("publish synthetic video during talkback: %v", err)
	default:
	}
	if err := handler.stream.WritePacketRTP(videoMedia, &rtp.Packet{
		Header: rtp.Header{
			Version: 2, PayloadType: 96, SequenceNumber: uint16(videoSequence.Load()),
			Timestamp: videoTimestamp.Load(), SSRC: 1, Marker: true,
		},
		Payload: []byte{0x67, 0x64, 0x00, 0x1f},
	}); err != nil {
		t.Fatal(err)
	}
	select {
	case <-p.ctx.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("incompatible SPS did not end WebRTC session")
	}
	p.mu.Lock()
	foundFormatError := false
	for _, event := range p.events {
		foundFormatError = foundFormatError || event.Code == "video_format_changed"
	}
	p.mu.Unlock()
	if !foundFormatError {
		t.Fatal("SPS change was not reported")
	}
	select {
	case count := <-videoPacketsReceived:
		if count < 400 {
			t.Fatalf("video did not continue through talkback and silence; received %d RTP packets", count)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("WebRTC video track did not close with its session")
	}
	response = requestWebRTC(t, api, http.MethodDelete, path, nil)
	if response.Code != http.StatusNoContent {
		t.Fatalf("DELETE: %d %s", response.Code, response.Body)
	}
	if response = requestWebRTC(t, api, http.MethodDelete, path, nil); response.Code != http.StatusNoContent {
		t.Fatalf("idempotent DELETE: %d", response.Code)
	}
	if fullFFmpeg != "" {
		sink.mu.Lock()
		defer sink.mu.Unlock()
		assertAACSignal(t, fullFFmpeg, sink.frames)
	}
}

func newLocalTestPeerConnection(t *testing.T) *webrtc.PeerConnection {
	t.Helper()
	settings := webrtc.SettingEngine{}
	settings.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	settings.SetIncludeLoopbackCandidate(true)
	api := webrtc.NewAPI(webrtc.WithSettingEngine(settings))
	peer, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	return peer
}

func TestWebRTCVideoOnlyDoesNotRequireEncoder(t *testing.T) {
	handler := &testRTSPHandler{}
	server := &gortsplib.Server{Handler: handler, RTSPAddress: "127.0.0.1:0"}
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	videoFormat := &format.H264{PayloadTyp: 96, PacketizationMode: 1,
		SPS: []byte{0x67, 0x42, 0xe0, 0x1f, 0xf4, 0x21, 0x32}, PPS: []byte{0x68, 0xc0}}
	video := &description.Media{Type: description.MediaTypeVideo, Formats: []format.Format{videoFormat}}
	handler.stream = &gortsplib.ServerStream{Server: server, Desc: &description.Session{Medias: []*description.Media{video}}}
	if err := handler.stream.Initialize(); err != nil {
		t.Fatal(err)
	}
	defer handler.stream.Close()
	started := make(chan struct{})
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		close(started)
		<-ctx.Done()
		return nil
	})
	manager.source = "127.0.0.1"
	manager.ffmpegPath = func() (string, error) { return "", errors.New("encoder absent") }
	host := "192.168.10.20"
	if _, _, err := manager.AddCamera(host, "test"); err != nil {
		t.Fatal(err)
	}
	<-started
	manager.mu.Lock()
	manager.cameras[host].RTSPURL = fmt.Sprintf("rtsp://127.0.0.1:%d/test", server.NetListener().Addr().(*net.TCPAddr).Port)
	manager.mu.Unlock()
	browser, err := webrtc.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	defer browser.Close()
	if _, err := browser.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
		t.Fatal(err)
	}
	offer, err := browser.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	api := manager.Handler()
	path := "/api/v1/cameras/" + host + "/webrtc/video_only"
	response := requestWebRTC(t, api, http.MethodPut, path, map[string]string{"offer": offer.SDP})
	if response.Code != http.StatusCreated {
		t.Fatalf("video-only offer: %d %s", response.Code, response.Body)
	}
	response = requestWebRTC(t, api, http.MethodGet, path+"/events?after=0", nil)
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"type":"answer"`) {
		t.Fatalf("video-only answer: %d %s", response.Code, response.Body)
	}
	if response = requestWebRTC(t, api, http.MethodDelete, path, nil); response.Code != http.StatusNoContent {
		t.Fatalf("video-only close: %d", response.Code)
	}
	// A microphone offer must fail explicitly when the encoder is unavailable.
	if _, err := browser.AddTransceiverFromKind(webrtc.RTPCodecTypeAudio, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionSendonly}); err != nil {
		t.Fatal(err)
	}
	offer, err = browser.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	response = requestWebRTC(t, api, http.MethodPut, path+"_mic", map[string]string{"offer": offer.SDP})
	if response.Code != http.StatusServiceUnavailable {
		t.Fatalf("microphone without encoder: %d %s", response.Code, response.Body)
	}
}

func TestWebRTCCloseReservesCameraUntilWorkerFinishes(t *testing.T) {
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error { <-ctx.Done(); return nil })
	p := newWebRTCPeer(manager.ctx, "192.168.10.20", "closing")
	p.workers.Add(1)
	manager.mu.Lock()
	manager.peers[p.host] = p
	manager.mu.Unlock()
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		done <- requestWebRTC(t, manager.Handler(), http.MethodDelete, "/api/v1/cameras/192.168.10.20/webrtc/closing", nil)
	}()
	deadline := time.After(time.Second)
	for {
		p.mu.Lock()
		closed := p.closed
		p.mu.Unlock()
		if closed {
			break
		}
		select {
		case <-deadline:
			t.Fatal("DELETE did not initiate closure")
		default:
			time.Sleep(time.Millisecond)
		}
	}
	if manager.webRTCPeer(p.host, p.id) == nil {
		t.Fatal("camera reservation released before workers stopped")
	}
	p.workers.Done()
	if response := <-done; response.Code != http.StatusNoContent {
		t.Fatalf("DELETE: %d", response.Code)
	}
	if manager.webRTCPeer(p.host, p.id) != nil {
		t.Fatal("camera reservation was not released")
	}
}

func TestWebRTCCloseActiveAtTombstoneLimit(t *testing.T) {
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error { <-ctx.Done(); return nil })
	p := newWebRTCPeer(manager.ctx, "192.168.10.20", "active")
	manager.mu.Lock()
	manager.peers[p.host] = p
	for i := 0; i < maxTombstones; i++ {
		manager.tombstones[fmt.Sprintf("old/%d", i)] = time.Now().Add(time.Minute)
	}
	manager.mu.Unlock()
	response := requestWebRTC(t, manager.Handler(), http.MethodDelete, "/api/v1/cameras/192.168.10.20/webrtc/active", nil)
	if response.Code != http.StatusNoContent {
		t.Fatalf("active close at capacity: %d", response.Code)
	}
}

func TestWebRTCCandidateLimit(t *testing.T) {
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error { <-ctx.Done(); return nil })
	p := newWebRTCPeer(manager.ctx, "192.168.10.20", "session")
	pc, err := webrtc.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	p.pc = pc
	p.remoteCandidates = webRTCMaxEvents
	manager.mu.Lock()
	manager.peers[p.host] = p
	manager.mu.Unlock()
	defer p.close()
	response := requestWebRTC(t, manager.Handler(), http.MethodPost,
		"/api/v1/cameras/192.168.10.20/webrtc/session/candidates",
		map[string]any{"candidate": map[string]string{"candidate": ""}})
	if response.Code != http.StatusTooManyRequests {
		t.Fatalf("candidate limit: %d %s", response.Code, response.Body)
	}
}

func TestWebRTCEstablishmentTimeout(t *testing.T) {
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error { <-ctx.Done(); return nil })
	p := newWebRTCPeer(manager.ctx, "192.168.10.20", "session")
	manager.mu.Lock()
	manager.peers[p.host] = p
	manager.wg.Add(1)
	manager.mu.Unlock()
	go manager.watchPeer(p, make(chan struct{}), 30*time.Millisecond)
	select {
	case <-p.ctx.Done():
	case <-time.After(time.Second):
		t.Fatal("establishment timeout did not close the peer")
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.events) == 0 || p.events[0].Code != "connection_timeout" {
		t.Fatalf("timeout event: %+v", p.events)
	}
}

func TestWebRTCCameraIsAvailableAfterPeerWorkersStop(t *testing.T) {
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error { <-ctx.Done(); return nil })
	host := "192.168.10.20"
	oldPeer := newWebRTCPeer(manager.ctx, host, "old-session")
	manager.mu.Lock()
	manager.peers[host] = oldPeer
	manager.wg.Add(1)
	manager.mu.Unlock()
	watchDone := make(chan struct{})
	go func() {
		manager.watchPeer(oldPeer, make(chan struct{}), time.Hour)
		close(watchDone)
	}()
	oldPeer.close()
	select {
	case <-watchDone:
	case <-time.After(time.Second):
		t.Fatal("closed peer did not release its camera")
	}
	if manager.webRTCPeer(host, oldPeer.id) != nil {
		t.Fatal("closed peer still reserves its camera")
	}
	newPeer := newWebRTCPeer(manager.ctx, host, "new-session")
	manager.mu.Lock()
	manager.peers[host] = newPeer
	manager.mu.Unlock()
	defer newPeer.close()
	if manager.webRTCPeer(host, newPeer.id) != newPeer {
		t.Fatal("camera could not accept a replacement WebRTC peer")
	}
}

func TestStopCameraWaitsForItsPeerOnly(t *testing.T) {
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error { <-ctx.Done(); return nil })
	for _, entry := range []struct{ host, name string }{{"192.168.10.20", "a"}, {"192.168.10.21", "b"}} {
		if _, _, err := manager.AddCamera(entry.host, entry.name); err != nil {
			t.Fatal(err)
		}
	}
	p := newWebRTCPeer(manager.ctx, "192.168.10.20", "session")
	other := newWebRTCPeer(manager.ctx, "192.168.10.21", "other")
	p.workers.Add(1)
	manager.mu.Lock()
	manager.peers[p.host], manager.peers[other.host] = p, other
	manager.mu.Unlock()
	defer other.close()
	done := make(chan error, 1)
	go func() { _, err := manager.StopCamera(context.Background(), p.host); done <- err }()
	select {
	case <-p.ctx.Done():
	case <-time.After(time.Second):
		p.workers.Done()
		t.Fatal("camera stop did not cancel its peer")
	}
	select {
	case <-other.ctx.Done():
		p.workers.Done()
		t.Fatal("stopping one camera canceled the other")
	default:
	}
	select {
	case <-done:
		p.workers.Done()
		t.Fatal("camera stop returned before its peer worker finished")
	default:
	}
	p.workers.Done()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("camera stop did not finish")
	}
}

func TestOpenTalkbackCancellationWhileCameraStopping(t *testing.T) {
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error { <-ctx.Done(); return nil })
	host := "192.168.10.20"
	if _, _, err := manager.AddCamera(host, "a"); err != nil {
		t.Fatal(err)
	}
	manager.mu.RLock()
	camera := manager.cameras[host]
	manager.mu.RUnlock()
	camera.operationMu.Lock()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { _, err := manager.OpenTalkback(ctx, host); done <- err }()
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			camera.operationMu.Unlock()
			t.Fatalf("open cancellation: %v", err)
		}
	case <-time.After(time.Second):
		camera.operationMu.Unlock()
		t.Fatal("open ignored cancellation while waiting for camera operation")
	}
	camera.operationMu.Unlock()
}

func TestWebRTCEventsLongPollAndTombstone(t *testing.T) {
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error { <-ctx.Done(); return nil })
	p := newWebRTCPeer(manager.ctx, "192.168.10.20", "abc")
	manager.mu.Lock()
	manager.peers[p.host] = p
	manager.mu.Unlock()
	api := manager.Handler()
	path := "/api/v1/cameras/192.168.10.20/webrtc/abc"
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() { done <- requestWebRTC(t, api, http.MethodGet, path+"/events?after=0", nil) }()
	select {
	case <-done:
		t.Fatal("events did not wait")
	case <-time.After(30 * time.Millisecond):
	}
	if err := p.answer("synthetic-answer"); err != nil {
		t.Fatal(err)
	}
	response := <-done
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), `"seq":1`) {
		t.Fatalf("events: %d %s", response.Code, response.Body)
	}
	if response = requestWebRTC(t, api, http.MethodGet, path+"/events?after=999", nil); response.Code != http.StatusBadRequest {
		t.Fatalf("invalid cursor: %d", response.Code)
	}
	if response = requestWebRTC(t, api, http.MethodDelete, path, nil); response.Code != http.StatusNoContent {
		t.Fatalf("delete: %d", response.Code)
	}
	manager.mu.RLock()
	_, tombstoned := manager.tombstones[webRTCKey("192.168.10.20", "abc")]
	manager.mu.RUnlock()
	if !tombstoned {
		t.Fatal("DELETE failed to tombstone session")
	}
}
