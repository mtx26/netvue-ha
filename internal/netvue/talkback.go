package netvue

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"os"
	"sync"
	"time"
)

const (
	maxTalkBytes    = 2 << 20
	maxTalkDuration = 30 * time.Second
	maxTalkSession  = 2 * time.Minute
)

var ErrTalkback = errors.New("Netvue talkback failed")

func talkFailure(reason string, err error) error {
	return atStage("talkback", withReason(reason, fmt.Errorf("%w: %s: %w", ErrTalkback, reason, err)))
}

// sessionWriter serializes whole packets, including deadlines and partial writes.
type sessionWriter struct {
	net.Conn
	mu sync.Mutex
}

type talkAudio struct {
	frames   [][]byte
	rate     int
	channels int
	delay    time.Duration
}

// RunTalkback runs a single bounded A/V session with experimental ADTS talkback.
// The caller must ensure that no other process is connected to this camera.
func RunTalkback(ctx context.Context, source, target, rtspURL string, duration time.Duration, path string, delay time.Duration) error {
	if duration <= 0 || duration > maxTalkSession {
		return talkFailure("invalid session duration", errors.New("talkback: session duration must be positive and at most 2 minutes"))
	}
	if delay < 0 || delay > 10*time.Second {
		return talkFailure("invalid delay", errors.New("talkback: delay must be between 0 and 10 seconds"))
	}
	file, err := os.Open(path)
	if err != nil {
		return talkFailure("cannot open ADTS file", fmt.Errorf("talkback: open ADTS file: %w", err))
	}
	defer file.Close()
	audio, err := readTalkAudio(file)
	if err != nil {
		return talkFailure("invalid or unreadable ADTS file", err)
	}
	audio.delay = delay
	if audio.duration()+delay >= duration {
		return talkFailure("session duration must exceed delay and audio duration", errors.New("talkback: session duration must exceed the delay plus audio duration"))
	}
	return runSession(ctx, source, target, rtspURL, duration, audio)
}

func readTalkAudio(reader io.Reader) (*talkAudio, error) {
	data, err := io.ReadAll(io.LimitReader(reader, maxTalkBytes+1))
	if err != nil {
		return nil, fmt.Errorf("read ADTS: %w", err)
	}
	if len(data) == 0 || len(data) > maxTalkBytes {
		return nil, errors.New("ADTS file is empty or exceeds 2 MiB")
	}
	rates := [...]int{96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350}
	audio := &talkAudio{}
	for offset := 0; offset < len(data); {
		if len(data)-offset < 7 {
			return nil, errors.New("truncated ADTS header")
		}
		h := data[offset:]
		// MPEG-4 AAC-LC, no CRC, one raw_data_block, explicit mono/stereo.
		index := int((h[2] >> 2) & 15)
		channels := int(h[2]&1)<<2 | int(h[3]>>6)
		if h[0] != 0xff || h[1]&0xfe != 0xf0 || h[1]&1 == 0 ||
			h[2]>>6 != 1 || index >= len(rates) || channels < 1 || channels > 2 || h[6]&3 != 0 {
			return nil, errors.New("unsupported ADTS: require MPEG-4 AAC-LC, no CRC, one block, mono or stereo")
		}
		rate := rates[index]
		if rate < 8000 || rate > 48000 {
			return nil, errors.New("unsupported ADTS sample rate")
		}
		length := int(h[3]&3)<<11 | int(h[4])<<3 | int(h[5]>>5)
		if length <= 7 || length > len(h) {
			return nil, errors.New("invalid or truncated ADTS frame")
		}
		if audio.rate == 0 {
			audio.rate, audio.channels = rate, channels
		} else if audio.rate != rate || audio.channels != channels {
			return nil, errors.New("ADTS format changes within file")
		}
		audio.frames = append(audio.frames, h[:length])
		if audio.duration() > maxTalkDuration {
			return nil, errors.New("ADTS audio exceeds 30 seconds")
		}
		offset += length
	}
	return audio, nil
}

func (audio *talkAudio) duration() time.Duration {
	return time.Duration(int64(len(audio.frames)) * 1024 * int64(time.Second) / int64(audio.rate))
}

func sendTalkDirection(ctx context.Context, conn net.Conn) error {
	packet, err := EncodeCommand([]map[string]any{{"Q": 5, "V": 1, "P": []int{3}}})
	if err != nil {
		return err
	}
	return sendSessionCommand(ctx, conn, packet, "talkback direction request")
}

func (audio *talkAudio) send(ctx context.Context, conn net.Conn) error {
	if audio.delay > 0 {
		if err := waitTalk(ctx, time.Now().Add(audio.delay)); err != nil {
			return err
		}
	}
	var next time.Time
	for i, frame := range audio.frames {
		if i > 0 {
			if err := waitTalk(ctx, next); err != nil {
				return err
			}
		}
		if i == 0 {
			if err := ctx.Err(); err != nil {
				return err
			}
			slog.Info("Netvue talkback audio sending")
		}
		now := time.Now()
		body := make([]byte, headerSize+5+len(frame))
		binary.LittleEndian.PutUint32(body[:4], uint32(5+len(frame)))
		binary.LittleEndian.PutUint16(body[14:16], packetMedia)
		body[headerSize] = 0x20
		binary.LittleEndian.PutUint32(body[headerSize+1:headerSize+5], uint32(now.UnixMilli()))
		copy(body[headerSize+5:], frame)
		if err := sendSessionCommand(ctx, conn, body, "talkback media"); err != nil {
			return err
		}
		// Never catch up by emitting a burst after a slow write.
		interval := time.Duration(int64(i+1)*1024*int64(time.Second)/int64(audio.rate) - int64(i)*1024*int64(time.Second)/int64(audio.rate))
		next = now.Add(interval)
	}
	return waitTalk(ctx, next)
}

func waitTalk(ctx context.Context, deadline time.Time) error {
	timer := time.NewTimer(time.Until(deadline))
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return ctx.Err()
	}
}
