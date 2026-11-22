package manager

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/gortsplib/v5/pkg/format/rtph264"
	"github.com/bluenviron/gortsplib/v5/pkg/format/rtpmpeg4audio"
	"github.com/bluenviron/gortsplib/v5/pkg/liberrors"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

func (p *webRTCPeer) openRTSP(rawURL string) (*gortsplib.Client, *description.Session, error) {
	u, err := base.ParseURL(rawURL)
	if err != nil {
		return nil, nil, err
	}
	protocol := gortsplib.ProtocolTCP
	for attempt := 0; ; attempt++ {
		if err := p.ctx.Err(); err != nil {
			return nil, nil, err
		}
		client := &gortsplib.Client{
			Scheme: u.Scheme, Host: u.Host, Protocol: &protocol,
			ReadTimeout: 12 * time.Second, WriteTimeout: 2 * time.Second,
		}
		if err := client.Start(); err != nil {
			return nil, nil, fmt.Errorf("start RTSP reader: %w", err)
		}
		p.mu.Lock()
		if p.closed {
			p.mu.Unlock()
			client.Close()
			return nil, nil, context.Canceled
		}
		p.rtsp = client
		p.mu.Unlock()
		desc, _, err := client.Describe(u)
		if err == nil {
			return client, desc, nil
		}
		client.Close()
		p.mu.Lock()
		if p.rtsp == client {
			p.rtsp = nil
		}
		p.mu.Unlock()
		var statusErr liberrors.ErrClientBadStatusCode
		if !errors.As(err, &statusErr) ||
			(statusErr.Code != base.StatusNotFound && statusErr.Code != base.StatusServiceUnavailable) {
			return nil, nil, fmt.Errorf("describe RTSP stream: %w", err)
		}
		if attempt == 0 {
			slog.Info("Netvue WebRTC waiting for RTSP publication", "camera", p.host, "status", statusErr.Code)
		}
		select {
		case <-time.After(500 * time.Millisecond):
		case <-p.ctx.Done():
			return nil, nil, fmt.Errorf("wait for RTSP publication: %w: %w", p.ctx.Err(), err)
		}
	}
}

func (p *webRTCPeer) setupRTSPAudio(
	client *gortsplib.Client,
	desc *description.Session,
	media *description.Media,
	audioFormat *format.MPEG4Audio,
	frames chan<- aacRTPFrame,
) error {
	decoder, err := audioFormat.CreateDecoder()
	if err != nil {
		return fmt.Errorf("create RTSP AAC decoder: %w", err)
	}
	if _, err := client.Setup(desc.BaseURL, media, 0, 0); err != nil {
		return fmt.Errorf("setup RTSP audio: %w", err)
	}
	// Damaged or surplus camera audio is dropped: it must not end the video.
	drop := func(reason string) {
		if n := p.droppedAudio.Add(1); n == 1 || n%500 == 0 {
			slog.Warn("Netvue WebRTC dropped camera audio", "reason", reason, "count", n)
		}
	}
	client.OnPacketRTP(media, audioFormat, func(packet *rtp.Packet) {
		units, err := decoder.Decode(packet)
		if err != nil {
			if !errors.Is(err, rtpmpeg4audio.ErrMorePacketsNeeded) {
				drop("invalid_packet")
			}
			return
		}
		pts, ok := client.PacketPTS(media, packet)
		if !ok {
			drop("timestamp_unavailable")
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
				drop("invalid_access_unit")
				continue
			}
			timestamp := sourceTimestamp + uint32(int64(i*samplesPerUnit)*48000/int64(sampleRate))
			select {
			case frames <- aacRTPFrame{data: frame, timestamp: timestamp}:
			case <-p.ctx.Done():
				return
			default:
				drop("encoder_overrun")
			}
		}
	})
	return nil
}

func (p *webRTCPeer) setupRTSPVideo(
	client *gortsplib.Client,
	desc *description.Session,
	media *description.Media,
	h264 *format.H264,
	sps []byte,
	track *webrtc.TrackLocalStaticRTP,
) error {
	if _, err := client.Setup(desc.BaseURL, media, 0, 0); err != nil {
		return fmt.Errorf("setup RTSP video: %w", err)
	}
	decoder, err := h264.CreateDecoder()
	if err != nil {
		return err
	}
	var invalidVideo sync.Once
	client.OnPacketRTP(media, h264, func(packet *rtp.Packet) {
		_, _ = client.PacketPTS(media, packet)
		accessUnit, err := decoder.Decode(packet)
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
	return nil
}
