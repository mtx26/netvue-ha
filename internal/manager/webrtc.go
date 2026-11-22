package manager

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/pion/ice/v4"
	"github.com/pion/webrtc/v4"
)

const (
	webRTCMaxEvents        = 128
	webRTCEstablishTimeout = 15 * time.Second
	// A connected peer is kept alive by ICE consent checks; this only bounds
	// sessions that were never closed by their owner.
	webRTCLifetime = 12 * time.Hour
	// Camera audio is optional: its encoder is restarted a few times, then
	// disabled without touching the video.
	webRTCAudioRestarts    = 3
	webRTCAudioStableAfter = 30 * time.Second
	webRTCMaxPeersPerHost  = 4
)

var errWebRTCVideoEncoderMissing = errors.New("WebRTC video encoder unavailable")

type webRTCEvent struct {
	Seq       uint64                   `json:"seq"`
	Type      string                   `json:"type"`
	Answer    string                   `json:"answer,omitempty"`
	Candidate *webrtc.ICECandidateInit `json:"candidate,omitempty"`
	Code      string                   `json:"code,omitempty"`
}

type webRTCPeer struct {
	id               string
	host             string
	ctx              context.Context
	cancel           context.CancelFunc
	mu               sync.Mutex
	closed           bool
	pc               *webrtc.PeerConnection
	iceMux           ice.UDPMux
	rtsp             *gortsplib.Client
	events           []webRTCEvent
	notify           chan struct{}
	pending          []webrtc.ICECandidateInit
	answered         bool
	remoteCandidates int
	workers          sync.WaitGroup
	workersStopped   chan struct{}
	connected        chan struct{}
	prepared         chan struct{}
	connectOnce      sync.Once
	talk             *talkController
	droppedAudio     atomic.Uint64
}

func newWebRTCPeer(parent context.Context, host, id string) *webRTCPeer {
	ctx, cancel := context.WithTimeout(parent, webRTCLifetime)
	return &webRTCPeer{
		ctx: ctx, cancel: cancel, host: host, id: id,
		notify: make(chan struct{}), connected: make(chan struct{}),
		workersStopped: make(chan struct{}), prepared: make(chan struct{}),
	}
}

func (p *webRTCPeer) key() string { return webRTCKey(p.host, p.id) }

// spawn starts a worker waited for by close; it fails once the peer is closed.
func (p *webRTCPeer) spawn(work func()) bool {
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		return false
	}
	p.workers.Add(1)
	p.mu.Unlock()
	go func() {
		defer p.workers.Done()
		work()
	}()
	return true
}

func (p *webRTCPeer) eventLocked(e webRTCEvent) bool {
	if len(p.events) >= webRTCMaxEvents {
		return false
	}
	e.Seq = uint64(len(p.events) + 1)
	p.events = append(p.events, e)
	close(p.notify)
	p.notify = make(chan struct{})
	return true
}

func (p *webRTCPeer) candidate(c *webrtc.ICECandidate) {
	if c == nil {
		return
	}
	value := c.ToJSON()
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		return
	}
	ok := true
	if !p.answered {
		if len(p.pending) >= webRTCMaxEvents-1 {
			ok = false
		} else {
			p.pending = append(p.pending, value)
		}
	} else {
		ok = p.eventLocked(webRTCEvent{Type: "candidate", Candidate: &value})
	}
	p.mu.Unlock()
	if !ok {
		p.fail("event_limit")
	}
}

func (p *webRTCPeer) answer(sdp string) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed {
		return context.Canceled
	}
	if !p.eventLocked(webRTCEvent{Type: "answer", Answer: sdp}) {
		return errors.New("event limit exceeded")
	}
	p.answered = true
	for i := range p.pending {
		if !p.eventLocked(webRTCEvent{Type: "candidate", Candidate: &p.pending[i]}) {
			return errors.New("event limit exceeded")
		}
	}
	p.pending = nil
	return nil
}

func (p *webRTCPeer) fail(code string) {
	p.mu.Lock()
	report := !p.closed && p.eventLocked(webRTCEvent{Type: "error", Code: code})
	p.mu.Unlock()
	if report && code != "session_closed" {
		slog.Warn("Netvue WebRTC session ended", "camera", p.host, "reason", code)
	}
	p.close()
}

func (p *webRTCPeer) close() {
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		return
	}
	p.closed = true
	pc, mux, client := p.pc, p.iceMux, p.rtsp
	close(p.notify)
	p.mu.Unlock()
	// No worker can be added after closed is set under mu.
	go func() {
		p.workers.Wait()
		close(p.workersStopped)
	}()
	p.cancel()
	if client != nil {
		client.Close()
	}
	if pc != nil {
		_ = pc.Close()
	}
	if mux != nil {
		_ = mux.Close()
	}
}

func (p *webRTCPeer) createPeerConnection(manager *Manager) (*webrtc.PeerConnection, error) {
	sourceIP := net.ParseIP(manager.source)
	if sourceIP == nil || sourceIP.To4() == nil {
		return nil, errors.New("WebRTC source address is invalid")
	}
	udpConn, err := net.ListenPacket("udp4", net.JoinHostPort(manager.source, "0"))
	if err != nil {
		return nil, fmt.Errorf("bind WebRTC ICE to source address: %w", err)
	}
	mux := ice.NewUDPMuxDefault(ice.UDPMuxParams{UDPConn: udpConn})
	settings := webrtc.SettingEngine{}
	settings.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	settings.SetIncludeLoopbackCandidate(sourceIP.IsLoopback())
	settings.SetIPFilter(func(ip net.IP) bool { return ip.Equal(sourceIP) })
	settings.SetICEUDPMux(mux)
	api := webrtc.NewAPI(webrtc.WithSettingEngine(settings))
	var config webrtc.Configuration
	if manager.turn != nil {
		config.ICEServers, _ = manager.turn.credentials(p.ctx)
	}
	pc, err := api.NewPeerConnection(config)
	if err != nil {
		_ = mux.Close()
		return nil, err
	}
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		_ = pc.Close()
		_ = mux.Close()
		return nil, context.Canceled
	}
	p.pc = pc
	p.iceMux = mux
	p.mu.Unlock()
	return pc, nil
}

func (p *webRTCPeer) start(manager *Manager, url, offer, ffmpeg string, microphone, cameraAudio bool) error {
	client, desc, err := p.openRTSP(url)
	if err != nil {
		return err
	}
	var h264 *format.H264
	media := desc.FindFormat(&h264)
	if media == nil {
		return errors.New("RTSP stream has no H.264 video")
	}
	sps, _ := h264.SafeParams()
	if len(sps) < 4 {
		return errors.New("RTSP stream has no usable H.264 SPS")
	}
	videoSPS, transcodeVideo, err := selectWebRTCVideo(offer, sps, microphone)
	if err != nil {
		return err
	}
	if transcodeVideo && ffmpeg == "" {
		ffmpeg, err = manager.webRTCFFmpeg()
		if err != nil {
			return errWebRTCVideoEncoderMissing
		}
	}
	var (
		audioMedia  *description.Media
		audioFormat *format.MPEG4Audio
		audioTrack  *webrtc.TrackLocalStaticRTP
		audioFrames chan aacRTPFrame
	)
	if cameraAudio {
		audioMedia = desc.FindFormat(&audioFormat)
		if audioMedia == nil || audioFormat.Config == nil {
			return errors.New("RTSP stream has no configured AAC audio")
		}
		if _, err := aacAccessUnitToADTS(audioFormat, []byte{0}); err != nil {
			return err
		}
		audioTrack, err = webrtc.NewTrackLocalStaticRTP(webrtc.RTPCodecCapability{
			MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2,
		}, "audio", "netvue")
		if err != nil {
			return err
		}
		audioFrames = make(chan aacRTPFrame, 32)
	}
	pc, err := p.createPeerConnection(manager)
	if err != nil {
		return err
	}
	pc.OnICECandidate(p.candidate)
	pc.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		if state == webrtc.PeerConnectionStateConnected {
			p.connectOnce.Do(func() { close(p.connected) })
		}
		if state == webrtc.PeerConnectionStateFailed {
			go p.fail("connection_failed")
		}
	})
	var gate *audioGate
	if audioTrack != nil {
		gate = newAudioGate(audioTrack)
	}
	if microphone {
		host := p.host
		p.talk = newTalkController(p.ctx,
			func(ctx context.Context) (talkSink, error) {
				if manager.openAudio != nil {
					return manager.openAudio(ctx, host)
				}
				return manager.OpenTalkback(ctx, host)
			},
			func(ctx context.Context, source rtpAudioReader, sink talkSink) error {
				return transcodeOpusToAAC(ctx, ffmpeg, source, sink)
			},
			func(muted bool) {
				if gate != nil {
					gate.setMuted(muted)
				}
			},
			p.spawn)
		pc.OnDataChannel(p.attachTalkChannel)
		pc.OnTrack(func(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
			if track.Kind() == webrtc.RTPCodecTypeAudio {
				p.spawn(func() { p.receiveMicrophone(track) })
			}
		})
		if !p.spawn(p.talk.run) {
			return context.Canceled
		}
	}
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: offer}); err != nil {
		return fmt.Errorf("invalid SDP offer: %w", err)
	}
	var track *webrtc.TrackLocalStaticRTP
	if transcodeVideo {
		adapter, err := manager.acquireVideoAdapter(p.host, url, ffmpeg, sps)
		if err != nil {
			return err
		}
		if !p.spawn(func() {
			defer manager.releaseVideoAdapter(p.host, adapter)
			select {
			case <-adapter.done:
				if p.ctx.Err() == nil {
					p.fail("video_transcode_failed")
				}
			case <-p.ctx.Done():
			}
		}) {
			manager.releaseVideoAdapter(p.host, adapter)
			return context.Canceled
		}
		track = adapter.track
	} else {
		track, err = webrtc.NewTrackLocalStaticRTP(webRTCH264Capability(videoSPS), "video", "netvue")
		if err != nil {
			return err
		}
	}
	sender, err := pc.AddTrack(track)
	if err != nil {
		return err
	}
	go drainRTPSender(sender)
	if cameraAudio {
		audioSender, err := pc.AddTrack(audioTrack)
		if err != nil {
			return err
		}
		go drainRTPSender(audioSender)
	}
	answer, err := pc.CreateAnswer(nil)
	if err != nil {
		return err
	}
	if err := validateWebRTCSDP(answer.SDP, videoSPS, false, microphone); err != nil {
		return err
	}
	if cameraAudio {
		ok, err := answerCanSendAudio(answer.SDP)
		if err != nil {
			return err
		}
		if !ok {
			return errors.New("WebRTC answer cannot send camera audio")
		}
	}
	if err := pc.SetLocalDescription(answer); err != nil {
		return err
	}
	if err := p.answer(answer.SDP); err != nil {
		return err
	}
	if cameraAudio {
		if err := p.setupRTSPAudio(client, desc, audioMedia, audioFormat, audioFrames); err != nil {
			return err
		}
	}
	if !transcodeVideo {
		if err := p.setupRTSPVideo(client, desc, media, h264, sps, track); err != nil {
			return err
		}
	}
	if !transcodeVideo || cameraAudio {
		if _, err := client.Play(nil); err != nil {
			return fmt.Errorf("play RTSP media: %w", err)
		}
		go func() {
			_ = client.Wait()
			if p.ctx.Err() == nil {
				p.fail("rtsp_stream_ended")
			}
		}()
	}
	if cameraAudio {
		if !p.spawn(func() { p.runCameraAudio(ffmpeg, audioFrames, gate) }) {
			return context.Canceled
		}
	}
	return nil
}

func drainRTPSender(sender *webrtc.RTPSender) {
	buffer := make([]byte, 1500)
	for {
		if _, _, err := sender.Read(buffer); err != nil {
			return
		}
	}
}

// runCameraAudio keeps the camera-to-browser audio encoder alive. Its failures
// never end the peer: the video does not depend on it.
func (p *webRTCPeer) runCameraAudio(ffmpeg string, frames <-chan aacRTPFrame, gate *audioGate) {
	for attempt := 0; ; attempt++ {
		gate.restart()
		started := time.Now()
		err := transcodeAACToOpusRTP(p.ctx, ffmpeg, frames, gate)
		if p.ctx.Err() != nil {
			return
		}
		if time.Since(started) >= webRTCAudioStableAfter {
			attempt = 0
		}
		if attempt >= webRTCAudioRestarts {
			slog.Error("Netvue WebRTC camera audio disabled; video continues", "error", err)
			return
		}
		slog.Warn("Netvue WebRTC camera audio restarting", "attempt", attempt+1, "error", err)
		select {
		case <-time.After(time.Second << attempt):
		case <-p.ctx.Done():
			return
		}
	}
}

func (p *webRTCPeer) attachTalkChannel(dc *webrtc.DataChannel) {
	c := p.talk
	if dc.Label() != talkChannelLabel || !c.claimChannel() {
		_ = dc.Close()
		return
	}
	c.setChannel(dc)
	ready := func() { c.send(talkMessage{Type: "state", State: talkStateListening}) }
	dc.OnOpen(ready)
	dc.OnMessage(func(message webrtc.DataChannelMessage) {
		if message.IsString {
			c.handleMessage(message.Data)
		}
	})
	dc.OnClose(func() { c.command(talkCommand{kind: "release"}) })
	if dc.ReadyState() == webrtc.DataChannelStateOpen {
		ready()
	}
}

// receiveMicrophone drains the browser microphone track. Packets are only used
// while a press is active; the control channel decides that, not their presence.
func (p *webRTCPeer) receiveMicrophone(track *webrtc.TrackRemote) {
	if !strings.EqualFold(track.Codec().MimeType, webrtc.MimeTypeOpus) {
		p.talk.setUnsupportedCodec()
		return
	}
	for p.ctx.Err() == nil {
		if err := track.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
			return
		}
		packet, _, err := track.ReadRTP()
		var networkErr net.Error
		if errors.As(err, &networkErr) && networkErr.Timeout() {
			continue
		}
		if err != nil {
			return
		}
		p.talk.deliver(packet)
	}
}

func findFFmpeg() (string, error) {
	return exec.LookPath("ffmpeg")
}

func (manager *Manager) webRTCFFmpeg() (string, error) {
	if manager.ffmpegPath != nil {
		return manager.ffmpegPath()
	}
	return findFFmpeg()
}
