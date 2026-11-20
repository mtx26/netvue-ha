package manager

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os/exec"
	"time"

	"github.com/bluenviron/gortsplib/v5/pkg/format/rtph264"
	"github.com/bluenviron/mediacommon/v2/pkg/formats/mpegts"
	"github.com/bluenviron/mediacommon/v2/pkg/formats/mpegts/codecs"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

var webRTCBaselineSPS = []byte{0x67, 0x42, 0xe0, 0x1f}

// webRTCVideoAdapter is one camera's H.264 adaptation, shared by all its
// WebRTC viewers so the encoder cost does not grow with the number of viewers.
type webRTCVideoAdapter struct {
	url    string
	track  *webrtc.TrackLocalStaticRTP
	cancel context.CancelFunc
	done   chan struct{}
	users  int
}

// broadcastTrack ignores per-viewer write errors: each viewer's own connection
// state ends that viewer, while the others keep receiving the shared stream.
type broadcastTrack struct{ track *webrtc.TrackLocalStaticRTP }

func (b broadcastTrack) WriteRTP(packet *rtp.Packet) error {
	_ = b.track.WriteRTP(packet)
	return nil
}

func webRTCH264Capability(sps []byte) webrtc.RTPCodecCapability {
	return webrtc.RTPCodecCapability{
		MimeType: webrtc.MimeTypeH264, ClockRate: 90000,
		SDPFmtpLine: fmt.Sprintf("level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=%x", sps[1:4]),
	}
}

// acquireVideoAdapter joins the camera's running adaptation or starts one.
// Each successful call must be paired with releaseVideoAdapter.
func (manager *Manager) acquireVideoAdapter(host, url, ffmpeg string, inputSPS []byte) (*webRTCVideoAdapter, error) {
	manager.mu.Lock()
	defer manager.mu.Unlock()
	if manager.closed {
		return nil, context.Canceled
	}
	if adapter := manager.adapters[host]; adapter != nil && adapter.url == url {
		adapter.users++
		return adapter, nil
	}
	track, err := webrtc.NewTrackLocalStaticRTP(webRTCH264Capability(webRTCBaselineSPS), "video", "netvue")
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(manager.ctx)
	adapter := &webRTCVideoAdapter{url: url, track: track, cancel: cancel, done: make(chan struct{}), users: 1}
	manager.adapters[host] = adapter
	manager.wg.Add(1)
	slog.Info("Netvue WebRTC adapting H.264", "camera", host, "input_profile_level", fmt.Sprintf("%x", inputSPS[1:4]))
	go func() {
		defer manager.wg.Done()
		err := manager.adaptVideo(ctx, ffmpeg, url, broadcastTrack{track})
		if ctx.Err() == nil {
			slog.Warn("Netvue WebRTC H.264 adaptation stopped", "camera", host, "error", err)
		}
		manager.mu.Lock()
		if manager.adapters[host] == adapter {
			delete(manager.adapters, host)
		}
		manager.mu.Unlock()
		cancel()
		close(adapter.done)
	}()
	return adapter, nil
}

func (manager *Manager) releaseVideoAdapter(host string, adapter *webRTCVideoAdapter) {
	manager.mu.Lock()
	defer manager.mu.Unlock()
	adapter.users--
	if adapter.users > 0 {
		return
	}
	if manager.adapters[host] == adapter {
		delete(manager.adapters, host)
	}
	adapter.cancel()
}

func transcodeH264ToWebRTC(ctx context.Context, ffmpeg, url string, track rtpTrackWriter) error {
	ctx, cancel := context.WithCancel(ctx)
	cmd := exec.CommandContext(ctx, ffmpeg,
		"-nostdin", "-hide_banner", "-loglevel", "error",
		"-rtsp_transport", "tcp", "-timeout", "5000000",
		"-analyzeduration", "500000", "-probesize", "500000",
		"-threads", "1", "-i", url,
		"-map", "0:v:0", "-an", "-filter_threads", "1",
		"-vf", "fps=15,scale=w='min(iw,1280)':h='min(ih,720)':force_original_aspect_ratio=decrease:force_divisible_by=2",
		"-c:v", "libx264", "-threads", "1", "-preset", "ultrafast", "-tune", "zerolatency",
		"-pix_fmt", "yuv420p", "-profile:v", "baseline", "-level:v", "3.1",
		"-g", "15", "-maxrate", "1500k", "-bufsize", "1500k",
		"-f", "mpegts", "-muxdelay", "0", "-flush_packets", "1", "pipe:1")
	cmd.WaitDelay = time.Second
	output, err := cmd.StdoutPipe()
	if err != nil {
		cancel()
		return err
	}
	if err := cmd.Start(); err != nil {
		cancel()
		return err
	}
	defer func() {
		cancel()
		_ = cmd.Wait()
	}()
	return packetizeWebRTCH264(output, track)
}

func packetizeWebRTCH264(output io.Reader, track rtpTrackWriter) error {
	reader, err := mpegts.NewReader(output)
	if err != nil {
		return fmt.Errorf("read transcoded video: %w", err)
	}
	for _, stream := range reader.Tracks() {
		if _, ok := stream.Codec.(*codecs.H264); !ok {
			continue
		}
		encoder := rtph264.Encoder{PayloadType: 96, PayloadMaxSize: 1188, PacketizationMode: 1}
		if err := encoder.Init(); err != nil {
			return err
		}
		reader.OnDataH264(stream, func(pts, _ int64, au [][]byte) error {
			for _, nalu := range au {
				if len(nalu) > 0 && nalu[0]&0x1f == 7 &&
					// libx264 uses 42c0; Chromium uses the equivalent 42e0.
					(len(nalu) < 4 || nalu[1] != 0x42 || nalu[2]&0x4f != 0x40 || nalu[3] > 0x1f) {
					return errors.New("transcoded H.264 is not constrained baseline level 3.1 or lower")
				}
			}
			packets, err := encoder.Encode(au)
			if err != nil {
				return err
			}
			for _, packet := range packets {
				packet.Timestamp = uint32(pts)
				if err := track.WriteRTP(packet); err != nil {
					return err
				}
			}
			return nil
		})
		for {
			if err := reader.Read(); err != nil {
				return err
			}
		}
	}
	return errors.New("transcoder produced no H.264 track")
}
