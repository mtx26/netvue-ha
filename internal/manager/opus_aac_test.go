package manager

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"sync"
	"testing"
	"time"

	"github.com/pion/opus"
	"github.com/pion/rtp"
)

type testAudioSource struct {
	ctx     context.Context
	packets chan *rtp.Packet
	idle    bool
}

func (s *testAudioSource) ReadRTP(ctx context.Context) (*rtp.Packet, error) {
	select {
	case p, ok := <-s.packets:
		if !ok {
			if s.idle {
				return nil, errAudioIdle
			}
			return nil, io.EOF
		}
		return p, nil
	case <-s.ctx.Done():
		return nil, s.ctx.Err()
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

type testTalkSink struct {
	mu          sync.Mutex
	frames      [][]byte
	first       chan struct{}
	closeEvents chan struct{}
}

func (*testTalkSink) AudioFormat() (int, int) { return 8000, 1 }
func (s *testTalkSink) WriteADTS(_ context.Context, data []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.frames = append(s.frames, append([]byte(nil), data...))
	if len(s.frames) == 1 {
		close(s.first)
	}
	return nil
}
func (s *testTalkSink) Close(context.Context) error {
	if s.closeEvents != nil {
		select {
		case s.closeEvents <- struct{}{}:
		default:
		}
	}
	return nil
}
func (*testTalkSink) Done() <-chan struct{} { return nil }
func (*testTalkSink) Err() error            { return nil }

func TestOpusAACStreamingFFmpeg(t *testing.T) {
	ffmpeg := os.Getenv("NETVUE_TEST_FFMPEG")
	if ffmpeg == "" {
		t.Skip("set NETVUE_TEST_FFMPEG to test the AAC encoder")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	source := &testAudioSource{ctx: ctx, packets: make(chan *rtp.Packet, 200)}
	sink := &testTalkSink{first: make(chan struct{})}
	done := make(chan error, 1)
	go func() { done <- transcodeOpusToAAC(ctx, ffmpeg, source, sink) }()
	for i := range 150 {
		source.packets <- &rtp.Packet{Header: rtp.Header{SequenceNumber: uint16(i), Timestamp: uint32(i * 960)}, Payload: []byte{0xf8, 0xff, 0xfe}}
	}
	select {
	case <-sink.first:
	case err := <-done:
		t.Fatalf("conversion stopped before streaming output: %v", err)
	case <-ctx.Done():
		t.Fatal("no AAC before input EOF")
	}
	close(source.packets)
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatalf("AAC encoder did not stop after input EOF: %v", ctx.Err())
	}
	sink.mu.Lock()
	defer sink.mu.Unlock()
	if len(sink.frames) < 10 {
		t.Fatalf("AAC frame count = %d", len(sink.frames))
	}
	for _, frame := range sink.frames {
		if len(frame) < 8 {
			t.Fatalf("short ADTS frame: %x", frame)
		}
		if frame[0] != 0xff || frame[1] != 0xf1 || frame[2]>>6 != 1 || (frame[2]>>2)&15 != 11 || (frame[3]>>6) != 1 {
			t.Fatalf("unexpected ADTS header: %x", frame[:7])
		}
	}
}

func TestOpusAACRealTimeFirstFrame(t *testing.T) {
	ffmpeg := os.Getenv("NETVUE_TEST_FFMPEG")
	if ffmpeg == "" {
		t.Skip("set NETVUE_TEST_FFMPEG to test the AAC encoder")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	source := &testAudioSource{ctx: ctx, packets: make(chan *rtp.Packet, 4)}
	sink := &testTalkSink{first: make(chan struct{})}
	done := make(chan error, 1)
	go func() { done <- transcodeOpusToAAC(ctx, ffmpeg, source, sink) }()
	start := time.Now()
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	for i := 0; i < 180; i++ {
		select {
		case <-ticker.C:
			packet := &rtp.Packet{Header: rtp.Header{SequenceNumber: uint16(i), Timestamp: uint32(i * 960)}, Payload: []byte{0xf8, 0xff, 0xfe}}
			select {
			case source.packets <- packet:
			case <-ctx.Done():
				t.Fatal("timed out while sending real-time Opus input")
			}
		case <-sink.first:
			t.Logf("first AAC frame after %s of real-time input", time.Since(start))
			close(source.packets)
			select {
			case err := <-done:
				if err != nil {
					t.Fatal(err)
				}
			case <-ctx.Done():
				t.Fatalf("AAC encoder did not stop after input EOF: %v", ctx.Err())
			}
			return
		case err := <-done:
			t.Fatalf("conversion stopped before first frame: %v", err)
		case <-ctx.Done():
			t.Fatal("no AAC before input EOF")
		}
	}
	t.Fatal("no AAC during 3.6 seconds of real-time audio")
}

func TestOpusAACEncoderEarlyExit(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	source := &testAudioSource{ctx: ctx, packets: make(chan *rtp.Packet)}
	sink := &testTalkSink{first: make(chan struct{})}
	done := make(chan error, 1)
	go func() { done <- transcodeOpusToAAC(ctx, executable, source, sink) }()
	select {
	case err := <-done:
		if err == nil || ctx.Err() != nil {
			t.Fatalf("early encoder exit not propagated promptly: %v", err)
		}
	case <-ctx.Done():
		t.Fatal("early encoder exit was not propagated before deadline")
	}
}

// Generate test packets locally; no camera capture or third-party media is used.
func syntheticToneOpus(t *testing.T, ffmpeg string) [][]byte {
	t.Helper()
	cmd := exec.Command(ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
		"-i", "sine=frequency=700:sample_rate=48000:duration=2",
		"-ac", "1", "-c:a", "libopus", "-application", "voip",
		"-frame_duration", "20", "-f", "ogg", "pipe:1")
	data, err := cmd.Output()
	if err != nil {
		t.Fatalf("generate synthetic Opus: %v", err)
	}
	var packet []byte
	var packets [][]byte
	for len(data) != 0 {
		if len(data) < 27 || string(data[:4]) != "OggS" {
			t.Fatal("invalid synthetic Ogg page")
		}
		segments := int(data[26])
		if len(data) < 27+segments {
			t.Fatal("truncated Ogg segment table")
		}
		table := data[27 : 27+segments]
		data = data[27+segments:]
		for _, size := range table {
			if len(data) < int(size) {
				t.Fatal("truncated Ogg payload")
			}
			packet = append(packet, data[:size]...)
			data = data[size:]
			if size < 255 {
				if !bytes.HasPrefix(packet, []byte("OpusHead")) && !bytes.HasPrefix(packet, []byte("OpusTags")) {
					packets = append(packets, append([]byte(nil), packet...))
				}
				packet = nil
			}
		}
	}
	if len(packet) != 0 || len(packets) < 20 {
		t.Fatalf("invalid synthetic Opus packet count: %d", len(packets))
	}
	return packets
}

func assertAACSignal(t *testing.T, ffmpeg string, frames [][]byte) {
	t.Helper()
	cmd := exec.Command(ffmpeg, "-hide_banner", "-loglevel", "error",
		"-f", "aac", "-i", "pipe:0", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1")
	cmd.Stdin = bytes.NewReader(bytes.Join(frames, nil))
	pcm, err := cmd.Output()
	if err != nil {
		t.Fatalf("independent AAC decode: %v", err)
	}
	nonzero := false
	for _, sample := range pcm {
		if sample != 0 {
			nonzero = true
			break
		}
	}
	if len(pcm) < 1600 || !nonzero {
		t.Fatalf("decoded AAC has no signal: %d PCM bytes", len(pcm))
	}
}

func TestOpusAACNonSilentSignal(t *testing.T) {
	ffmpeg := os.Getenv("NETVUE_TEST_FFMPEG_FULL")
	if ffmpeg == "" {
		t.Skip("set NETVUE_TEST_FFMPEG_FULL to test synthetic Opus and AAC decoding")
	}
	packets := syntheticToneOpus(t, ffmpeg)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	source := &testAudioSource{ctx: ctx, packets: make(chan *rtp.Packet, len(packets))}
	for i, packet := range packets {
		source.packets <- &rtp.Packet{Header: rtp.Header{SequenceNumber: uint16(i), Timestamp: uint32(i * 960)}, Payload: packet}
	}
	close(source.packets)
	sink := &testTalkSink{first: make(chan struct{})}
	done := make(chan error, 1)
	go func() { done <- transcodeOpusToAAC(ctx, ffmpeg, source, sink) }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatalf("Opus-to-AAC conversion did not stop before deadline: %v", ctx.Err())
	}
	sink.mu.Lock()
	defer sink.mu.Unlock()
	assertAACSignal(t, ffmpeg, sink.frames)
}

func TestOpusRTPReorderLossAndDTX(t *testing.T) {
	ctx := context.Background()
	source := &testAudioSource{ctx: ctx, packets: make(chan *rtp.Packet, 4)}
	for _, entry := range []struct {
		seq uint16
		ts  uint32
	}{{1, 0}, {3, 1920}, {2, 960}, {5, 9600}} {
		source.packets <- &rtp.Packet{Header: rtp.Header{SequenceNumber: entry.seq, Timestamp: entry.ts}, Payload: []byte{0xf8, 0xff, 0xfe}}
	}
	close(source.packets)
	decoder, err := opus.NewDecoderWithOutput(8000, 1)
	if err != nil {
		t.Fatal(err)
	}
	var pcm bytes.Buffer
	if err := readOpusPCM(ctx, source, &decoder, &pcm, 8000, 1); err != nil {
		t.Fatal(err)
	}
	// Four 20ms packets plus 140ms timestamp gap for one lost packet.
	if pcm.Len() != (4*160+1120)*2 {
		t.Fatalf("PCM bytes = %d, want %d", pcm.Len(), (4*160+1120)*2)
	}
}

func TestOpusDTXDoesNotReplaySilence(t *testing.T) {
	ctx := context.Background()
	source := &testAudioSource{ctx: ctx, packets: make(chan *rtp.Packet, 2)}
	for _, entry := range []struct {
		seq uint16
		ts  uint32
	}{{1, 0}, {2, 48000}} {
		source.packets <- &rtp.Packet{Header: rtp.Header{SequenceNumber: entry.seq, Timestamp: entry.ts}, Payload: []byte{0xf8, 0xff, 0xfe}}
	}
	close(source.packets)
	decoder, _ := opus.NewDecoderWithOutput(8000, 1)
	var pcm bytes.Buffer
	if err := readOpusPCM(ctx, source, &decoder, &pcm, 8000, 1); err != nil {
		t.Fatal(err)
	}
	if pcm.Len() != 2*160*2 {
		t.Fatalf("DTX added stale silence: %d bytes", pcm.Len())
	}
}

func TestOpusIdleFlushesPendingPackets(t *testing.T) {
	source := &testAudioSource{ctx: context.Background(), packets: make(chan *rtp.Packet, 2), idle: true}
	for _, entry := range []struct {
		seq uint16
		ts  uint32
	}{{1, 0}, {3, 1920}} {
		source.packets <- &rtp.Packet{
			Header:  rtp.Header{SequenceNumber: entry.seq, Timestamp: entry.ts},
			Payload: []byte{0xf8, 0xff, 0xfe},
		}
	}
	close(source.packets)
	decoder, err := opus.NewDecoderWithOutput(8000, 1)
	if err != nil {
		t.Fatal(err)
	}
	var pcm bytes.Buffer
	if err := readOpusPCM(context.Background(), source, &decoder, &pcm, 8000, 1); !errors.Is(err, errAudioIdle) {
		t.Fatalf("readOpusPCM error = %v, want microphone idle", err)
	}
	if pcm.Len() != 3*160*2 {
		t.Fatalf("PCM bytes = %d, want final packets and one lost frame flushed", pcm.Len())
	}
}
