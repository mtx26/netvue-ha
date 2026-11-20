package manager

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"testing"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/h264"
	"github.com/bluenviron/mediacommon/v2/pkg/formats/mpegts"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

func TestWebRTCTranscodesMain41RTSP(t *testing.T) {
	ffmpeg := os.Getenv("NETVUE_TEST_FFMPEG")
	if ffmpeg == "" {
		t.Skip("set NETVUE_TEST_FFMPEG to test H.264 transcoding")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, ffmpeg, "-hide_banner", "-loglevel", "error",
		"-f", "lavfi", "-i", "testsrc2=size=1280x960:rate=15", "-frames:v", "15",
		"-c:v", "libx264", "-threads", "1", "-preset", "veryfast", "-tune", "zerolatency",
		"-profile:v", "main", "-level:v", "4.1", "-f", "mpegts", "pipe:1")
	fixture, err := cmd.Output()
	if err != nil {
		t.Fatalf("generate input: %v", err)
	}
	reader, err := mpegts.NewReader(bytes.NewReader(fixture))
	if err != nil {
		t.Fatal(err)
	}
	var inputSPS, inputPPS []byte
	var frames [][][]byte
	reader.OnDataH264(reader.Tracks()[0], func(_, _ int64, au [][]byte) error {
		for _, nalu := range au {
			switch nalu[0] & 0x1f {
			case 7:
				inputSPS = nalu
			case 8:
				inputPPS = nalu
			}
		}
		frames = append(frames, au)
		return nil
	})
	for {
		if err := reader.Read(); errors.Is(err, io.EOF) {
			break
		} else if err != nil {
			t.Fatal(err)
		}
	}
	if len(inputSPS) < 4 || inputSPS[1] != 0x4d || inputSPS[3] != 0x29 {
		t.Fatalf("fixture is not Main 4.1: %x", inputSPS)
	}
	handler := &testRTSPHandler{}
	server := &gortsplib.Server{Handler: handler, RTSPAddress: "127.0.0.1:0"}
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	f := &format.H264{PayloadTyp: 96, PacketizationMode: 1, SPS: inputSPS, PPS: inputPPS}
	v := &description.Media{Type: description.MediaTypeVideo, Formats: []format.Format{f}}
	handler.stream = &gortsplib.ServerStream{Server: server, Desc: &description.Session{Medias: []*description.Media{v}}}
	if err := handler.stream.Initialize(); err != nil {
		t.Fatal(err)
	}
	defer handler.stream.Close()
	encoder, err := f.CreateEncoder()
	if err != nil {
		t.Fatal(err)
	}
	finished := make(chan error, 1)
	go func() {
		ticker := time.NewTicker(time.Second / 15)
		defer ticker.Stop()
		for i := 0; ; i++ {
			select {
			case <-ctx.Done():
				finished <- nil
				return
			case <-ticker.C:
			}
			packets, err := encoder.Encode(frames[i%len(frames)])
			if err != nil {
				finished <- err
				return
			}
			for _, packet := range packets {
				packet.Timestamp = uint32(i * 6000)
				if err := handler.stream.WritePacketRTP(v, packet); err != nil {
					finished <- err
					return
				}
			}
		}
	}()
	defer func() {
		cancel()
		if err := <-finished; err != nil {
			t.Errorf("publish fixture: %v", err)
		}
	}()

	started := make(chan struct{})
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		close(started)
		<-ctx.Done()
		return nil
	})
	manager.source = "127.0.0.1"
	manager.ffmpegPath = func() (string, error) { return ffmpeg, nil }
	host := "192.168.10.20"
	if _, _, err := manager.AddCamera(host, "test"); err != nil {
		t.Fatal(err)
	}
	<-started
	manager.mu.Lock()
	manager.cameras[host].RTSPURL = fmt.Sprintf("rtsp://%s/test", server.NetListener().Addr())
	manager.mu.Unlock()
	browser := newLocalTestPeerConnection(t)
	defer browser.Close()
	received := make(chan *rtp.Packet, 32)
	browser.OnTrack(func(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
		defer close(received)
		for {
			packet, _, err := track.ReadRTP()
			if err != nil {
				return
			}
			select {
			case <-ctx.Done():
				return
			case received <- packet:
			}
		}
	})
	if _, err := browser.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{
		Direction: webrtc.RTPTransceiverDirectionRecvonly,
	}); err != nil {
		t.Fatal(err)
	}
	offer, err := browser.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	gathered := webrtc.GatheringCompletePromise(browser)
	if err := browser.SetLocalDescription(offer); err != nil {
		t.Fatal(err)
	}
	select {
	case <-gathered:
	case <-ctx.Done():
		t.Fatal("no browser ICE candidates")
	}
	api := manager.Handler()
	path := "/api/v1/cameras/" + host + "/webrtc/baseline"
	response := requestWebRTC(t, api, http.MethodPut, path, map[string]string{"offer": browser.LocalDescription().SDP})
	if response.Code != http.StatusCreated {
		t.Fatalf("PUT: %d %s", response.Code, response.Body)
	}
	var after uint64
	gotCandidate := false
	for !gotCandidate {
		response := requestWebRTC(t, api, http.MethodGet, fmt.Sprintf("%s/events?after=%d", path, after), nil)
		var reply struct{ Events []webRTCEvent }
		if response.Code != http.StatusOK || json.Unmarshal(response.Body.Bytes(), &reply) != nil {
			t.Fatalf("events: %d %s", response.Code, response.Body)
		}
		for _, event := range reply.Events {
			after = event.Seq
			switch event.Type {
			case "answer":
				if err := validateWebRTCSDP(event.Answer, webRTCBaselineSPS, false, false); err != nil {
					t.Fatal(err)
				}
				if err := browser.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: event.Answer}); err != nil {
					t.Fatal(err)
				}
			case "candidate":
				if err := browser.AddICECandidate(*event.Candidate); err != nil {
					t.Fatal(err)
				}
				gotCandidate = true
			case "error":
				t.Fatalf("WebRTC failed: %s", event.Code)
			}
		}
	}
	decoder, err := f.CreateDecoder()
	if err != nil {
		t.Fatal(err)
	}
	var output []byte
	var sps h264.SPS
	var firstPTS uint32
	framesReceived := 0
	for framesReceived < 4 {
		var p *rtp.Packet
		select {
		case <-ctx.Done():
			t.Fatalf("received %d transcoded frames", framesReceived)
		case packet, ok := <-received:
			if !ok {
				t.Fatal("WebRTC video track closed early")
			}
			p = packet
		}
		au, err := decoder.Decode(p)
		if err != nil {
			continue
		}
		for _, nalu := range au {
			if nalu[0]&0x1f == 7 {
				if err := sps.Unmarshal(nalu); err != nil {
					t.Fatal(err)
				}
			}
		}
		if sps.Width() == 0 {
			continue
		}
		data, err := (h264.AnnexB(au)).Marshal()
		if err != nil {
			t.Fatal(err)
		}
		output = append(output, data...)
		framesReceived++
		if framesReceived == 1 {
			firstPTS = p.Timestamp
		}
		if p.Timestamp-firstPTS != uint32(framesReceived-1)*6000 {
			t.Fatalf("unexpected RTP timestamp delta: %d", p.Timestamp-firstPTS)
		}
	}
	if response := requestWebRTC(t, api, http.MethodDelete, path, nil); response.Code != http.StatusNoContent {
		t.Fatalf("DELETE: %d %s", response.Code, response.Body)
	}
	if sps.ProfileIdc != 66 || sps.LevelIdc != 31 || sps.Width() != 960 || sps.Height() != 720 {
		t.Fatalf("unexpected output: profile=%d level=%d size=%dx%d", sps.ProfileIdc, sps.LevelIdc, sps.Width(), sps.Height())
	}
	decode := exec.CommandContext(ctx, ffmpeg, "-hide_banner", "-loglevel", "error", "-xerror",
		"-f", "h264", "-i", "pipe:0", "-frames:v", "4", "-f", "null", "-")
	decode.Stdin = bytes.NewReader(output)
	if log, err := decode.CombinedOutput(); err != nil {
		t.Fatalf("decode transcoded frames: %v: %s", err, log)
	}
}
