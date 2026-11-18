package manager

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os/exec"
	"time"

	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/mpeg4audio"
	mediopus "github.com/bluenviron/mediacommon/v2/pkg/codecs/opus"
	"github.com/pion/rtp"
)

var aacSampleRates = [...]int{96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350}

type aacRTPFrame struct {
	data      []byte
	timestamp uint32
}

func aacAccessUnitToADTS(audio *format.MPEG4Audio, accessUnit []byte) ([]byte, error) {
	if audio == nil || audio.Config == nil || audio.Config.Type != mpeg4audio.ObjectTypeAACLC {
		return nil, errors.New("RTSP audio is not AAC-LC")
	}
	config := audio.Config
	sampleRateIndex := -1
	for i, rate := range aacSampleRates {
		if rate == config.SampleRate {
			sampleRateIndex = i
			break
		}
	}
	if sampleRateIndex < 0 || config.ChannelConfig == 0 || config.ChannelConfig > 7 ||
		len(accessUnit) == 0 || len(accessUnit)+7 > 8191 {
		return nil, errors.New("unsupported AAC access unit")
	}
	frameLength := len(accessUnit) + 7
	header := []byte{
		0xff, 0xf1,
		byte(1<<6 | sampleRateIndex<<2 | int(config.ChannelConfig)>>2),
		byte(int(config.ChannelConfig&3)<<6 | frameLength>>11),
		byte(frameLength >> 3),
		byte((frameLength&7)<<5 | 0x1f),
		0xfc,
	}
	return append(header, accessUnit...), nil
}

type rtpTrackWriter interface {
	WriteRTP(*rtp.Packet) error
}

func transcodeAACToOpusRTP(ctx context.Context, ffmpeg string, frames <-chan aacRTPFrame, track rtpTrackWriter) error {
	listener, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		return fmt.Errorf("listen for local Opus RTP: %w", err)
	}
	defer listener.Close()
	port := listener.LocalAddr().(*net.UDPAddr).Port

	commandCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	cmd := exec.CommandContext(commandCtx, ffmpeg,
		"-hide_banner", "-loglevel", "error",
		"-probesize", "32", "-analyzeduration", "0",
		"-f", "aac", "-i", "pipe:0",
		"-vn", "-ar", "48000", "-ac", "2",
		"-c:a", "libopus", "-application", "voip", "-frame_duration", "20",
		"-b:a", "24k", "-flush_packets", "1",
		"-payload_type", "111", "-f", "rtp",
		fmt.Sprintf("rtp://127.0.0.1:%d?pkt_size=1200&rtcpport=%d", port, port))
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return fmt.Errorf("open AAC encoder input: %w", err)
	}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start Opus encoder: %w", err)
	}
	waitDone := make(chan error, 1)
	go func() { waitDone <- cmd.Wait() }()
	inputDone := make(chan error, 1)
	firstTimestamp := make(chan uint32, 1)
	go func() {
		firstFrame := true
		for {
			select {
			case <-commandCtx.Done():
				inputDone <- commandCtx.Err()
				return
			case frame, ok := <-frames:
				if !ok {
					inputDone <- stdin.Close()
					return
				}
				if firstFrame {
					firstTimestamp <- frame.timestamp
					firstFrame = false
				}
				if _, err := stdin.Write(frame.data); err != nil {
					inputDone <- fmt.Errorf("write AAC encoder input: %w", err)
					return
				}
			}
		}
	}()

	var (
		inputErr           error
		inputRead          bool
		waitErr            error
		waitRead           bool
		timestampOffset    uint32
		timestampOffsetSet bool
		receivedRTP        bool
		lastTimestamp      uint32
		lastDuration       uint32
		haveLastPacket     bool
	)
	cleanup := func() {
		cancel()
		_ = stdin.Close()
		if !waitRead {
			waitErr = <-waitDone
			waitRead = true
		}
		if !inputRead {
			inputErr = <-inputDone
			inputRead = true
		}
	}
	buffer := make([]byte, 1500)
	for {
		if err := listener.SetReadDeadline(time.Now().Add(250 * time.Millisecond)); err != nil {
			cleanup()
			return fmt.Errorf("set Opus RTP read deadline: %w", err)
		}
		n, _, readErr := listener.ReadFrom(buffer)
		if readErr != nil {
			if networkErr, ok := readErr.(net.Error); ok && networkErr.Timeout() {
				select {
				case err := <-waitDone:
					waitErr, waitRead = err, true
					if !inputRead {
						cancel()
						inputErr = <-inputDone
						inputRead = true
					}
					if ctx.Err() != nil {
						return ctx.Err()
					}
					if inputErr != nil && !errors.Is(inputErr, context.Canceled) {
						return inputErr
					}
					if waitErr != nil {
						return fmt.Errorf("Opus encoder failed: %w", waitErr)
					}
					if inputErr != nil {
						return errors.New("Opus encoder exited before AAC input ended")
					}
					if !receivedRTP {
						return errors.New("Opus encoder produced no RTP audio")
					}
					return nil
				case err := <-inputDone:
					inputErr, inputRead = err, true
					if err != nil && !errors.Is(err, context.Canceled) {
						cleanup()
						return err
					}
				default:
				}
				if err := ctx.Err(); err != nil {
					cleanup()
					return err
				}
				continue
			}
			cleanup()
			return fmt.Errorf("read Opus RTP: %w", readErr)
		}
		var packet rtp.Packet
		if err := packet.Unmarshal(buffer[:n]); err != nil || packet.PayloadType != 111 {
			continue
		}
		if !timestampOffsetSet {
			select {
			case sourceTimestamp := <-firstTimestamp:
				timestampOffset = sourceTimestamp - packet.Timestamp
				timestampOffsetSet = true
			case <-ctx.Done():
				cleanup()
				return ctx.Err()
			}
		}
		packet.Timestamp += timestampOffset
		duration := mediopus.PacketDuration2(packet.Payload)
		if duration <= 0 || duration > 5760 {
			cleanup()
			return errors.New("Opus encoder produced an invalid RTP packet duration")
		}
		if haveLastPacket {
			nextTimestamp := lastTimestamp + lastDuration
			if int32(packet.Timestamp-nextTimestamp) < 0 {
				packet.Timestamp = nextTimestamp
			}
		}
		lastTimestamp = packet.Timestamp
		lastDuration = uint32(duration)
		haveLastPacket = true
		if err := track.WriteRTP(&packet); err != nil {
			cleanup()
			return fmt.Errorf("write WebRTC audio: %w", err)
		}
		receivedRTP = true
	}
}
