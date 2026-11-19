package manager

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"time"

	"github.com/bluenviron/gortsplib/v5/pkg/format/rtph264"
	"github.com/bluenviron/mediacommon/v2/pkg/formats/mpegts"
	"github.com/bluenviron/mediacommon/v2/pkg/formats/mpegts/codecs"
)

var webRTCBaselineSPS = []byte{0x67, 0x42, 0xe0, 0x1f}

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
