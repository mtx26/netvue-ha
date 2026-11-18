package manager

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/gortsplib/v5/pkg/format/rtph264"
	"github.com/bluenviron/gortsplib/v5/pkg/format/rtpmpeg4audio"
	"github.com/pion/ice/v4"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

const (
	webRTCMaxEvents        = 128
	webRTCLifetime         = 10 * time.Minute
	webRTCEstablishTimeout = 15 * time.Second
	webRTCAudioIdleTimeout = 10 * time.Second
)

var errAudioIdle = errors.New("microphone audio idle")

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
	connected        chan struct{}
	connectOnce      sync.Once
}

func newWebRTCPeer(parent context.Context, host, id string) *webRTCPeer {
	ctx, cancel := context.WithTimeout(parent, webRTCLifetime)
	return &webRTCPeer{ctx: ctx, cancel: cancel, host: host, id: id, notify: make(chan struct{}), connected: make(chan struct{})}
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
	if !p.closed {
		_ = p.eventLocked(webRTCEvent{Type: "error", Code: code})
	}
	p.mu.Unlock()
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

func (p *webRTCPeer) start(manager *Manager, url, offer, ffmpeg string, microphone, cameraAudio bool) error {
	u, err := base.ParseURL(url)
	if err != nil {
		return err
	}
	protocol := gortsplib.ProtocolTCP
	client := &gortsplib.Client{
		Scheme: u.Scheme, Host: u.Host, Protocol: &protocol,
		ReadTimeout: 10 * time.Second, WriteTimeout: 2 * time.Second,
	}
	if err := client.Start(); err != nil {
		return fmt.Errorf("start RTSP reader: %w", err)
	}
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		client.Close()
		return context.Canceled
	}
	p.rtsp = client
	p.mu.Unlock()
	desc, _, err := client.Describe(u)
	if err != nil {
		return fmt.Errorf("describe RTSP stream: %w", err)
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
	if err := validateWebRTCSDP(offer, sps, true, microphone); err != nil {
		return err
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
	sourceIP := net.ParseIP(manager.source)
	if sourceIP == nil || sourceIP.To4() == nil {
		return errors.New("WebRTC source address is invalid")
	}
	udpConn, err := net.ListenPacket("udp4", net.JoinHostPort(manager.source, "0"))
	if err != nil {
		return fmt.Errorf("bind WebRTC ICE to source address: %w", err)
	}
	mux := ice.NewUDPMuxDefault(ice.UDPMuxParams{UDPConn: udpConn})
	settings := webrtc.SettingEngine{}
	settings.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	settings.SetIncludeLoopbackCandidate(sourceIP.IsLoopback())
	settings.SetIPFilter(func(ip net.IP) bool { return ip.Equal(sourceIP) })
	settings.SetICEUDPMux(mux)
	api := webrtc.NewAPI(webrtc.WithSettingEngine(settings))
	pc, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		_ = mux.Close()
		return err
	}
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		_ = pc.Close()
		_ = mux.Close()
		return context.Canceled
	}
	p.pc = pc
	p.iceMux = mux
	p.mu.Unlock()
	pc.OnICECandidate(p.candidate)
	pc.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		if state == webrtc.PeerConnectionStateConnected {
			p.connectOnce.Do(func() { close(p.connected) })
		}
		if state == webrtc.PeerConnectionStateFailed {
			go p.fail("connection_failed")
		}
	})
	pc.OnTrack(func(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
		p.mu.Lock()
		if p.closed {
			p.mu.Unlock()
			return
		}
		p.workers.Add(1)
		p.mu.Unlock()
		go func() {
			defer p.workers.Done()
			p.receiveAudio(manager, track, ffmpeg)
		}()
	})
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: offer}); err != nil {
		return fmt.Errorf("invalid SDP offer: %w", err)
	}
	track, err := webrtc.NewTrackLocalStaticRTP(webrtc.RTPCodecCapability{
		MimeType: webrtc.MimeTypeH264, ClockRate: 90000,
		SDPFmtpLine: fmt.Sprintf("level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=%02x%02x%02x", sps[1], sps[2], sps[3]),
	}, "video", "netvue")
	if err != nil {
		return err
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
	if err := validateWebRTCSDP(answer.SDP, sps, false, microphone); err != nil {
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
	if _, err := client.Setup(desc.BaseURL, media, 0, 0); err != nil {
		return fmt.Errorf("setup RTSP video: %w", err)
	}
	var audioDecoder *rtpmpeg4audio.Decoder
	if cameraAudio {
		audioDecoder, err = audioFormat.CreateDecoder()
		if err != nil {
			return fmt.Errorf("create RTSP AAC decoder: %w", err)
		}
		if _, err := client.Setup(desc.BaseURL, audioMedia, 0, 0); err != nil {
			return fmt.Errorf("setup RTSP audio: %w", err)
		}
		var invalidAudio sync.Once
		client.OnPacketRTP(audioMedia, audioFormat, func(packet *rtp.Packet) {
			units, err := audioDecoder.Decode(packet)
			if err != nil {
				if errors.Is(err, rtpmpeg4audio.ErrMorePacketsNeeded) {
					return
				}
				invalidAudio.Do(func() { go p.fail("audio_invalid") })
				return
			}
			pts, ok := client.PacketPTS(audioMedia, packet)
			if !ok {
				invalidAudio.Do(func() { go p.fail("audio_timestamp_unavailable") })
				return
			}
			sampleRate := audioFormat.ClockRate()
			sourceTimestamp := uint32(pts * 48000 / int64(sampleRate))
			samplesPerUnit := 1024
			if audioFormat.Config.FrameLengthFlag {
				samplesPerUnit = 960
			}
			for i, unit := range units {
				frame, err := aacAccessUnitToADTS(audioFormat, unit)
				if err != nil {
					invalidAudio.Do(func() { go p.fail("audio_invalid") })
					return
				}
				timestamp := sourceTimestamp + uint32(int64(i*samplesPerUnit)*48000/int64(sampleRate))
				select {
				case audioFrames <- aacRTPFrame{data: frame, timestamp: timestamp}:
				case <-p.ctx.Done():
					return
				default:
					invalidAudio.Do(func() { go p.fail("audio_overrun") })
					return
				}
			}
		})
	}
	videoDecoder, err := h264.CreateDecoder()
	if err != nil {
		return err
	}
	var invalidVideo sync.Once
	client.OnPacketRTP(media, h264, func(packet *rtp.Packet) {
		_, _ = client.PacketPTS(media, packet)
		accessUnit, err := videoDecoder.Decode(packet)
		if err != nil && !errors.Is(err, rtph264.ErrMorePacketsNeeded) &&
			!errors.Is(err, rtph264.ErrNonStartingPacketAndNoPrevious) {
			invalidVideo.Do(func() { go p.fail("video_invalid") })
			return
		}
		for _, nalu := range accessUnit {
			if nalu[0]&0x1f == 7 && (len(nalu) < 4 || nalu[1] != sps[1] || nalu[2] != sps[2] || nalu[3] > sps[3]) {
				invalidVideo.Do(func() { go p.fail("video_format_changed") })
				return
			}
		}
		_ = track.WriteRTP(packet)
	})
	if _, err := client.Play(nil); err != nil {
		return fmt.Errorf("play RTSP video: %w", err)
	}
	if cameraAudio {
		p.mu.Lock()
		if p.closed {
			p.mu.Unlock()
			return context.Canceled
		}
		p.workers.Add(1)
		p.mu.Unlock()
		go func() {
			defer p.workers.Done()
			if err := transcodeAACToOpusRTP(p.ctx, ffmpeg, audioFrames, audioTrack); err != nil && p.ctx.Err() == nil {
				p.fail("audio_conversion_failed")
			}
		}()
	}
	go func() {
		_ = client.Wait()
		if p.ctx.Err() == nil {
			p.fail("video_unavailable")
		}
	}()
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

func (p *webRTCPeer) receiveAudio(manager *Manager, track *webrtc.TrackRemote, ffmpeg string) {
	if !strings.EqualFold(track.Codec().MimeType, webrtc.MimeTypeOpus) {
		p.fail("audio_codec_unsupported")
		return
	}
	source := &remoteAudio{track: track}
	for {
		first, err := source.ReadRTP(p.ctx)
		if errors.Is(err, errAudioIdle) {
			continue
		}
		if err != nil {
			if p.ctx.Err() == nil && !source.ended {
				p.fail("audio_failed")
			}
			return
		}
		var sink talkSink
		if manager.openAudio != nil {
			sink, err = manager.openAudio(p.ctx, p.host)
		} else {
			sink, err = manager.OpenTalkback(p.ctx, p.host)
		}
		if err != nil {
			p.fail("audio_unavailable")
			return
		}
		watchDone := make(chan struct{})
		watchExited := make(chan struct{})
		go func() {
			defer close(watchExited)
			select {
			case <-sink.Done():
				if p.ctx.Err() == nil && sink.Err() != nil {
					p.fail("audio_unavailable")
				}
			case <-watchDone:
			case <-p.ctx.Done():
			}
		}()
		err = transcodeOpusToAAC(p.ctx, ffmpeg, &prefetchedAudio{first: first, source: source}, sink)
		closeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		closeErr := sink.Close(closeCtx)
		cancel()
		close(watchDone)
		<-watchExited
		if p.ctx.Err() != nil {
			return
		}
		if closeErr != nil {
			p.fail("audio_unavailable")
			return
		}
		if source.ended {
			return
		}
		if errors.Is(err, errAudioIdle) {
			continue
		}
		if err != nil {
			p.fail("audio_failed")
			return
		}
		p.fail("audio_failed")
		return
	}
}

type remoteAudio struct {
	track      *webrtc.TrackRemote
	lastPacket time.Time
	ended      bool
}

func (r *remoteAudio) ReadRTP(ctx context.Context) (*rtp.Packet, error) {
	for {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if err := r.track.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
			return nil, err
		}
		packet, _, err := r.track.ReadRTP()
		var networkErr net.Error
		if errors.As(err, &networkErr) && networkErr.Timeout() {
			if !r.lastPacket.IsZero() && time.Since(r.lastPacket) >= webRTCAudioIdleTimeout {
				return nil, errAudioIdle
			}
			continue
		}
		if errors.Is(err, io.EOF) {
			r.ended = true
		}
		if err == nil {
			r.lastPacket = time.Now()
		}
		return packet, err
	}
}

type prefetchedAudio struct {
	first  *rtp.Packet
	source *remoteAudio
}

func (r *prefetchedAudio) ReadRTP(ctx context.Context) (*rtp.Packet, error) {
	if r.first != nil {
		packet := r.first
		r.first = nil
		return packet, nil
	}
	return r.source.ReadRTP(ctx)
}

func findFFmpeg() (string, error) {
	return exec.LookPath("ffmpeg")
}
