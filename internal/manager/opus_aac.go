package manager

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os/exec"
	"strconv"

	"github.com/pion/opus"
	"github.com/pion/rtp"
)

// talkSink is intentionally synchronous: it bounds buffered microphone audio.
type talkSink interface {
	AudioFormat() (int, int)
	WriteADTS(context.Context, []byte) error
	Close(context.Context) error
	Done() <-chan struct{}
	Err() error
}

type rtpAudioReader interface {
	ReadRTP(context.Context) (*rtp.Packet, error)
}

// transcodeOpusToAAC decodes Opus to PCM in-process and uses FFmpeg's native
// AAC-LC encoder. The RTP reorder buffer is bounded to three packets.
func transcodeOpusToAAC(ctx context.Context, ffmpeg string, source rtpAudioReader, sink talkSink) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	rate, channels := sink.AudioFormat()
	decoder, err := opus.NewDecoderWithOutput(rate, channels)
	if err != nil {
		return fmt.Errorf("unsupported selected audio format: %w", err)
	}
	cmd := exec.CommandContext(ctx, ffmpeg,
		"-hide_banner", "-loglevel", "error",
		"-probesize", "32", "-analyzeduration", "0",
		"-f", "s16le", "-ar", strconv.Itoa(rate), "-ac", strconv.Itoa(channels),
		"-i", "pipe:0", "-vn", "-c:a", "aac", "-profile:a", "aac_low",
		"-b:a", "24k", "-flush_packets", "1", "-f", "adts", "pipe:1")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start AAC encoder: %w", err)
	}
	result := make(chan error, 1)
	inputDone := make(chan struct{})
	go func() {
		var outputErr error
		defer func() {
			if outputErr != nil {
				cancel()
			}
			result <- outputErr
		}()
		for {
			header := make([]byte, 7)
			if _, err := io.ReadFull(stdout, header); err != nil {
				if errors.Is(err, io.EOF) {
					select {
					case <-inputDone:
					default:
						outputErr = errors.New("AAC encoder exited before input ended")
					}
				} else {
					outputErr = fmt.Errorf("read ADTS header: %w", err)
				}
				return
			}
			length := int(header[3]&3)<<11 | int(header[4])<<3 | int(header[5]>>5)
			if length <= 7 || length > 8191 {
				outputErr = errors.New("invalid AAC encoder ADTS frame")
				return
			}
			frame := make([]byte, length)
			copy(frame, header)
			if _, err := io.ReadFull(stdout, frame[7:]); err != nil {
				outputErr = fmt.Errorf("read ADTS payload: %w", err)
				return
			}
			if err := sink.WriteADTS(ctx, frame); err != nil {
				outputErr = err
				return
			}
		}
	}()
	decodeErr := readOpusPCM(ctx, source, &decoder, stdin, rate, channels)
	close(inputDone)
	_ = stdin.Close()
	if decodeErr != nil {
		_ = cmd.Process.Kill()
	}
	outputErr := <-result
	waitErr := cmd.Wait()
	if outputErr != nil {
		return outputErr
	}
	if decodeErr != nil {
		return decodeErr
	}
	if waitErr != nil {
		return fmt.Errorf("AAC encoder failed: %w", waitErr)
	}
	return nil
}

func readOpusPCM(ctx context.Context, source rtpAudioReader, decoder *opus.Decoder, out io.Writer, rate, channels int) error {
	samples := make([]int16, rate*120/1000*channels)
	pcm := make([]byte, len(samples)*2)
	var expectedSeq uint16
	var expectedTimestamp uint32
	haveSeq, haveTimestamp := false, false
	pending := make(map[uint16]*rtp.Packet, 3)
	process := func(packet *rtp.Packet, lost uint16) error {
		// WebRTC padding consumes a sequence number, but carries no audio or clock update.
		if len(packet.Payload) == 0 && packet.Padding && packet.PaddingSize > 0 {
			return nil
		}
		if len(packet.Payload) == 0 || len(packet.Payload) > 8192 {
			return fmt.Errorf("invalid Opus payload: bytes=%d padding=%t padding_bytes=%d",
				len(packet.Payload), packet.Padding, packet.PaddingSize)
		}
		if haveTimestamp {
			gap := int32(packet.Timestamp - expectedTimestamp)
			if gap < 0 {
				return errors.New("Opus RTP timestamp moved backwards")
			}
			// A forward gap without packet loss is DTX: real time has
			// already elapsed, so don't replay silence after it.
			if lost > 0 {
				if gap > 2*48000 || int64(gap)*int64(rate)%48000 != 0 {
					return errors.New("Opus RTP loss exceeds concealment limit")
				}
				slog.Warn("Microphone RTP packets lost", "count", lost)
				silence := make([]byte, int(int64(gap)*int64(rate)/48000)*channels*2)
				if err := writePCM(out, silence); err != nil {
					return err
				}
			}
		}
		count, err := decoder.DecodeToInt16(packet.Payload, samples)
		if err != nil {
			return fmt.Errorf("decode Opus: %w", err)
		}
		if count <= 0 || count*channels > len(samples) {
			return errors.New("invalid decoded Opus duration")
		}
		for i, value := range samples[:count*channels] {
			binary.LittleEndian.PutUint16(pcm[2*i:], uint16(value))
		}
		if err := writePCM(out, pcm[:count*channels*2]); err != nil {
			return err
		}
		expectedTimestamp = packet.Timestamp + uint32(count*48000/rate)
		haveTimestamp = true
		return nil
	}
	drain := func(force bool) error {
		for len(pending) > 0 {
			if packet := pending[expectedSeq]; packet != nil {
				delete(pending, expectedSeq)
				if err := process(packet, 0); err != nil {
					return err
				}
				expectedSeq++
				continue
			}
			if !force && len(pending) < 3 {
				return nil
			}
			var nearest *rtp.Packet
			var delta uint16
			for seq, packet := range pending {
				d := seq - expectedSeq
				if nearest == nil || d < delta {
					nearest, delta = packet, d
				}
			}
			delete(pending, nearest.SequenceNumber)
			if err := process(nearest, delta); err != nil {
				return err
			}
			expectedSeq = nearest.SequenceNumber + 1
		}
		return nil
	}
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		packet, err := source.ReadRTP(ctx)
		if err != nil {
			if errors.Is(err, io.EOF) {
				return drain(true)
			}
			return err
		}
		if packet == nil {
			return errors.New("missing Opus RTP packet")
		}
		if !haveSeq {
			expectedSeq, haveSeq = packet.SequenceNumber, true
		}
		delta := int16(packet.SequenceNumber - expectedSeq)
		if delta < 0 {
			continue // duplicate or too late for the bounded reorder window
		}
		if _, exists := pending[packet.SequenceNumber]; !exists {
			pending[packet.SequenceNumber] = packet
		}
		if err := drain(delta >= 3); err != nil {
			return err
		}
	}
}

func writePCM(w io.Writer, data []byte) error {
	for len(data) != 0 {
		n, err := w.Write(data)
		if err != nil {
			return err
		}
		if n <= 0 {
			return io.ErrShortWrite
		}
		data = data[n:]
	}
	return nil
}
