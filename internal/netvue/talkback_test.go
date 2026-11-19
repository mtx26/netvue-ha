package netvue

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func testADTS(index byte, channels byte, payload byte) []byte {
	return []byte{0xff, 0xf1, 0x40 | index<<2 | channels>>2, channels << 6, 1, 0x1f, 0xfc, payload}
}

func mustReadTalkAudio(t *testing.T, data []byte) *talkAudio {
	t.Helper()
	audio, err := readTalkAudio(bytes.NewReader(data))
	if err != nil {
		t.Fatalf("readTalkAudio() failed: %v", err)
	}
	return audio
}

func TestTalkADTSValidation(t *testing.T) {
	frame := testADTS(11, 1, 42)
	for _, tc := range []struct {
		name string
		data []byte
		ok   bool
	}{
		{"valid", append(append([]byte{}, frame...), frame...), true},
		{"empty", nil, false},
		{"truncated header", frame[:5], false},
		{"truncated frame", frame[:7], false},
		{"trailing garbage", append(append([]byte{}, frame...), 1), false},
		{"profile", append([]byte{0xff, 0xf1, 0xac}, frame[3:]...), false},
		{"crc", append([]byte{0xff, 0xf0}, frame[2:]...), false},
		{"mpeg2", append([]byte{0xff, 0xf9}, frame[2:]...), false},
		{"blocks", append(append([]byte{}, frame[:6]...), 0xfd, 42), false},
		{"mixed rate", append(append([]byte{}, frame...), testADTS(8, 1, 1)...), false},
		{"mixed channels", append(append([]byte{}, frame...), testADTS(11, 2, 1)...), false},
		{"no channels", testADTS(11, 0, 1), false},
		{"too large", make([]byte, maxTalkBytes+1), false},
		{"too long", bytes.Repeat(frame, 235), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			audio, err := readTalkAudio(bytes.NewReader(tc.data))
			if (err == nil) != tc.ok {
				t.Fatalf("readTalkAudio() = %v", err)
			}
			if tc.ok && (audio.rate != 8000 || audio.channels != 1 || audio.duration() != 256*time.Millisecond) {
				t.Fatalf("unexpected audio: %+v", audio)
			}
		})
	}
	if _, err := readTalkAudio(errorReader{}); err == nil {
		t.Fatal("reader error ignored")
	}
}

func TestRunTalkbackRejectsInputBeforeNetwork(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sample.aac")
	if err := os.WriteFile(path, testADTS(11, 1, 1), 0600); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		duration time.Duration
		delay    time.Duration
		path     string
		want     string
	}{
		{0, 0, path, "session duration"},
		{3 * time.Minute, 0, path, "session duration"},
		{time.Second, 11 * time.Second, path, "delay"},
		{time.Second, 0, path + ".missing", "open ADTS"},
		{100 * time.Millisecond, 0, path, "must exceed"},
		{time.Second, time.Second, path, "must exceed"},
	} {
		err := RunTalkback(context.Background(), "invalid", "invalid", "", tc.duration, tc.path, tc.delay)
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Fatalf("error = %v, want %q", err, tc.want)
		}
	}
}

func TestTalkADTSFromFFmpeg(t *testing.T) {
	ffmpeg := os.Getenv("NETVUE_TEST_FFMPEG")
	if ffmpeg == "" {
		t.Skip("set NETVUE_TEST_FFMPEG for the synthetic AAC encoder test")
	}
	path := filepath.Join(t.TempDir(), "sample.aac")
	cmd := exec.Command(ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
		"-i", "sine=frequency=440:sample_rate=8000:duration=1",
		"-ac", "1", "-ar", "8000", "-c:a", "aac", "-profile:a", "aac_low",
		"-b:a", "24k", "-f", "adts", path)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("encode AAC: %v: %s", err, output)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	audio, err := readTalkAudio(bytes.NewReader(data))
	if err != nil || audio.rate != 8000 || audio.channels != 1 || audio.duration() < time.Second || audio.duration() > 2*time.Second {
		t.Fatalf("FFmpeg ADTS audio=%+v error=%v", audio, err)
	}
}

type errorReader struct{}

func (errorReader) Read([]byte) (int, error) { return 0, errors.New("read failure") }

type talkLogHandler struct {
	slog.Handler
	sent chan struct{}
}

func (h *talkLogHandler) Handle(ctx context.Context, record slog.Record) error {
	if record.Message == "Netvue talkback audio sent" {
		select {
		case h.sent <- struct{}{}:
		default:
		}
	}
	return h.Handler.Handle(ctx, record)
}

func TestTalkSendsMediaWhileReceivingAudioVideo(t *testing.T) {
	client, server := testPipe(t)
	var logs bytes.Buffer
	logged := make(chan struct{}, 1)
	previousLogger := slog.Default()
	slog.SetDefault(slog.New(&talkLogHandler{Handler: slog.NewTextHandler(&logs, nil), sent: logged}))
	defer slog.SetDefault(previousLogger)
	audio, err := readTalkAudio(bytes.NewReader(bytes.Repeat(testADTS(11, 1, 7), 3)))
	if err != nil {
		t.Fatal(err)
	}
	audio.delay = 80 * time.Millisecond
	frameDuration := audio.duration() / time.Duration(len(audio.frames))
	minimumCadence := frameDuration / 2
	publisher := &audioTestPublisher{}
	ctx, cancel := context.WithTimeoutCause(context.Background(), 2*time.Second, errSessionDurationElapsed)
	defer cancel()
	result := make(chan error, 1)
	go func() { result <- receiveLiveTalk(ctx, client, publisher, time.Second, 3*time.Second, true, audio) }()
	ack := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 0, "P": [][]any{{1, 1, 2, []any{8000, 1, 1}}}}})
	if err := writeFull(server, ack); err != nil {
		t.Fatal(err)
	}
	direction, err := ReadPacket(server)
	if err != nil {
		t.Fatal(err)
	}
	units, err := ParseUnits(direction.Body)
	if err != nil || len(units) != 1 || !commandHasValues(units[0], 5, 1, []int{3}) {
		t.Fatalf("direction = %s, error %v", direction.Body, err)
	}
	// No media is sent before the first published video frame.
	if err := server.SetReadDeadline(time.Now().Add(40 * time.Millisecond)); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadPacket(server); err == nil {
		t.Fatal("talkback started before video")
	}
	if err := server.SetReadDeadline(time.Time{}); err != nil {
		t.Fatal(err)
	}
	if err := writeFull(server, mediaPacket(0, []byte{1})); err != nil {
		t.Fatal(err)
	}
	start := time.Now()
	incoming := make(chan error, 1)
	go func() {
		err := writeFull(server, audioTestPacket(false, 40, []byte{1, 2, 3}))
		if err == nil {
			err = writeFull(server, mediaPacket(33, []byte{2}))
		}
		incoming <- err
	}()
	var previous time.Time
	for i := 0; i < 3; i++ {
		packet, err := ReadPacket(server)
		now := time.Now()
		if err != nil {
			t.Fatal(err)
		}
		if packet.Kind != packetMedia || packet.Body[0] != 0x20 || !bytes.Equal(packet.Body[5:], testADTS(11, 1, 7)) {
			t.Fatalf("invalid media packet: %+v", packet)
		}
		pts := binary.LittleEndian.Uint32(packet.Body[1:5])
		if delta := int32(uint32(now.UnixMilli()) - pts); delta < 0 || delta > 1000 {
			t.Fatalf("PTS not Unix milliseconds: delta=%d", delta)
		}
		if i == 0 && now.Sub(start) < audio.delay*3/4 {
			t.Fatalf("delay too short: %v", now.Sub(start))
		}
		if i > 0 && now.Sub(previous) < minimumCadence {
			t.Fatalf("frames sent too quickly: %v", now.Sub(previous))
		}
		previous = now
	}
	if err := <-incoming; err != nil {
		t.Fatal(err)
	}
	select {
	case <-logged:
	case err := <-result:
		t.Fatalf("LIVE stopped before talkback completed: %v", err)
	case <-time.After(time.Second):
		t.Fatal("talkback completion not logged")
	}
	if time.Since(previous) < minimumCadence {
		t.Fatal("talkback completed before the last AAC frame duration")
	}
	// Both packets are sent after the completion log, hence after the final
	// outgoing AAC frame and its duration. A command acts as a read barrier.
	if err := writeFull(server, audioTestPacket(false, 80, []byte{4, 5, 6})); err != nil {
		t.Fatalf("audio after talkback: %v", err)
	}
	if err := writeFull(server, mediaPacket(66, []byte{3})); err != nil {
		t.Fatalf("video after talkback: %v", err)
	}
	barrier := mustEncodeCommand(t, []map[string]any{{"A": 99}})
	if err := writeFull(server, barrier); err != nil {
		t.Fatal(err)
	}
	if err := server.SetReadDeadline(time.Now().Add(200 * time.Millisecond)); err != nil {
		t.Fatal(err)
	}
	if packet, err := ReadPacket(server); err == nil {
		t.Fatalf("unexpected second talkback sender: %+v", packet)
	} else {
		var networkErr net.Error
		if !errors.As(err, &networkErr) || !networkErr.Timeout() {
			t.Fatalf("LIVE connection closed after talkback: %v", err)
		}
	}
	if err := server.SetReadDeadline(time.Time{}); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-result:
		t.Fatalf("LIVE stopped before duration: %v", err)
	default:
	}
	if err := <-result; err != nil {
		t.Fatal(err)
	}
	if len(publisher.audioFrames) != 2 || publisher.audioFrames[1].timestamp != 80 || publisher.PublishedFrames() < 3 {
		t.Fatalf("incoming media: audio=%d video=%d", len(publisher.audioFrames), publisher.PublishedFrames())
	}
	startLog := strings.Index(logs.String(), "Netvue talkback audio sending")
	endLog := strings.Index(logs.String(), "Netvue talkback audio sent")
	if startLog < 0 || endLog <= startLog || strings.Count(logs.String(), "Netvue talkback audio sending") != 1 ||
		strings.Count(logs.String(), "Netvue talkback audio sent") != 1 {
		t.Fatalf("talkback logs = %q", logs.String())
	}
}

func TestTalkSelectionAndCancellation(t *testing.T) {
	for _, tc := range []struct {
		name string
		ack  map[string]any
		want string
	}{
		{"rejected", map[string]any{"A": 3, "V": 1, "C": 9}, "selection rejected"},
		{"missing audio", map[string]any{"A": 3, "V": 1, "C": 0}, "does not match"},
		{"mismatch", map[string]any{"A": 3, "V": 1, "C": 0, "P": [][]any{{1, 1, 2, []any{16000, 1, 1}}}}, "does not match"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			client, server := testPipe(t)
			audio := mustReadTalkAudio(t, testADTS(11, 1, 1))
			done := make(chan error, 1)
			go func() {
				done <- receiveLiveTalk(context.Background(), client, &audioTestPublisher{}, time.Second, time.Second, true, audio)
			}()
			packet := mustEncodeCommand(t, []map[string]any{tc.ack})
			if err := writeFull(server, packet); err != nil {
				t.Fatal(err)
			}
			if err := <-done; err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("error = %v", err)
			}
		})
	}
	t.Run("cancel blocked media write", func(t *testing.T) {
		client, server := testPipe(t)
		audio := mustReadTalkAudio(t, testADTS(11, 1, 1))
		ctx, cancel := context.WithCancel(context.Background())
		done := make(chan error, 1)
		go func() {
			done <- receiveLiveTalk(ctx, client, &audioTestPublisher{}, time.Second, time.Second, true, audio)
		}()
		ack := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 0, "P": [][]any{{1, 1, 2, []any{8000, 1, 1}}}}})
		if err := writeFull(server, ack); err != nil {
			t.Fatal(err)
		}
		if _, err := ReadPacket(server); err != nil {
			t.Fatal(err)
		}
		if err := writeFull(server, mediaPacket(0, []byte{1})); err != nil {
			t.Fatal(err)
		}
		cancel()
		select {
		case err := <-done:
			if !errors.Is(err, context.Canceled) {
				t.Fatalf("error = %v", err)
			}
		case <-time.After(time.Second):
			t.Fatal("cancellation leaked sender")
		}
	})
	t.Run("direction refusal", func(t *testing.T) {
		client, server := testPipe(t)
		audio := mustReadTalkAudio(t, testADTS(11, 1, 1))
		done := make(chan error, 1)
		go func() {
			done <- receiveLiveTalk(context.Background(), client, &audioTestPublisher{}, time.Second, time.Second, true, audio)
		}()
		ack := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 0, "P": [][]any{{1, 1, 2, []any{8000, 1, 1}}}}})
		if err := writeFull(server, ack); err != nil {
			t.Fatal(err)
		}
		if _, err := ReadPacket(server); err != nil {
			t.Fatal(err)
		}
		refusal := mustEncodeCommand(t, []map[string]any{{"A": 5, "V": 1, "C": 1}})
		if err := writeFull(server, refusal); err != nil {
			t.Fatal(err)
		}
		if err := <-done; err == nil || !strings.Contains(err.Error(), "direction rejected") {
			t.Fatalf("error = %v", err)
		}
	})
	t.Run("cancel delay", func(t *testing.T) {
		client, server := testPipe(t)
		audio := mustReadTalkAudio(t, testADTS(11, 1, 1))
		audio.delay = 10 * time.Second
		ctx, cancel := context.WithCancel(context.Background())
		done := make(chan error, 1)
		go func() {
			done <- receiveLiveTalk(ctx, client, &audioTestPublisher{}, time.Second, time.Second, true, audio)
		}()
		ack := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 0, "P": [][]any{{1, 1, 2, []any{8000, 1, 1}}}}})
		if err := writeFull(server, ack); err != nil {
			t.Fatal(err)
		}
		if _, err := ReadPacket(server); err != nil {
			t.Fatal(err)
		}
		if err := writeFull(server, mediaPacket(0, []byte{1})); err != nil {
			t.Fatal(err)
		}
		cancel()
		select {
		case err := <-done:
			if !errors.Is(err, context.Canceled) {
				t.Fatalf("error = %v", err)
			}
		case <-time.After(time.Second):
			t.Fatal("delayed sender failed to stop")
		}
	})
	t.Run("duration expires during delay", func(t *testing.T) {
		client, server := testPipe(t)
		audio := mustReadTalkAudio(t, testADTS(11, 1, 1))
		audio.delay = time.Second
		ctx, cancel := context.WithTimeoutCause(context.Background(), 200*time.Millisecond, errSessionDurationElapsed)
		defer cancel()
		done := make(chan error, 1)
		go func() {
			done <- receiveLiveTalk(ctx, client, &audioTestPublisher{}, time.Second, time.Second, true, audio)
		}()
		ack := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 0, "P": [][]any{{1, 1, 2, []any{8000, 1, 1}}}}})
		if err := writeFull(server, ack); err != nil {
			t.Fatal(err)
		}
		if _, err := ReadPacket(server); err != nil {
			t.Fatal(err)
		}
		if err := writeFull(server, mediaPacket(0, []byte{1})); err != nil {
			t.Fatal(err)
		}
		select {
		case err := <-done:
			if err == nil {
				t.Fatal("unfinished talkback reported success")
			}
		case <-time.After(2 * time.Second):
			t.Fatal("timed out waiting for session")
		}
	})
}

type shortWriteConn struct{ net.Conn }

type failTalkMediaConn struct {
	net.Conn
	err error
}

func (c failTalkMediaConn) Write(p []byte) (int, error) {
	if len(p) >= headerSize && binary.LittleEndian.Uint16(p[14:16]) == packetMedia {
		return 0, c.err
	}
	return c.Conn.Write(p)
}

func TestTalkSendFailureInterruptsLive(t *testing.T) {
	client, server := testPipe(t)
	wantErr := errors.New("synthetic write failure")
	audio := mustReadTalkAudio(t, testADTS(11, 1, 1))
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- receiveLiveTalk(ctx, failTalkMediaConn{Conn: client, err: wantErr}, &audioTestPublisher{}, time.Second, time.Second, true, audio)
	}()
	ack := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 0, "P": [][]any{{1, 1, 2, []any{8000, 1, 1}}}}})
	if err := writeFull(server, ack); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadPacket(server); err != nil {
		t.Fatal(err)
	}
	if err := writeFull(server, mediaPacket(0, []byte{1})); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if !errors.Is(err, wantErr) || !errors.Is(err, ErrTalkback) {
			t.Fatalf("sender failure lost: %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("sender failure did not interrupt LIVE")
	}
}

func (c shortWriteConn) Write(p []byte) (int, error) {
	if len(p) > 3 {
		p = p[:3]
	}
	return c.Conn.Write(p)
}

func TestSessionWriterSerializesWholePackets(t *testing.T) {
	client, server := testPipe(t)
	writer := &sessionWriter{Conn: shortWriteConn{client}}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	var wg sync.WaitGroup
	errs := make(chan error, 2)
	for n := 0; n < 2; n++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			for i := 0; i < 40; i++ {
				packet, err := EncodeCommand([]map[string]any{{"Q": n, "P": fmt.Sprint(i)}})
				if err != nil {
					errs <- err
					return
				}
				if n == 1 {
					packet = makePacketKind(6, 0, packetMedia, []byte{0x20, 1, 2, 3, 4, byte(i)})
				}
				if err := sendSessionCommand(ctx, writer, packet, "test"); err != nil {
					errs <- err
					return
				}
			}
		}(n)
	}
	for i := 0; i < 80; i++ {
		packet, err := ReadPacket(server)
		if err != nil {
			t.Fatal(err)
		}
		if packet.Kind == packetCommand {
			if _, err := ParseUnits(packet.Body); err != nil {
				t.Fatal(err)
			}
		} else if packet.Kind != packetMedia || len(packet.Body) != 6 || packet.Body[0] != 0x20 {
			t.Fatalf("interleaved packet: %+v", packet)
		}
	}

	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatal(err)
	}
}

func TestTalkSessionsAreIndependent(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	first, firstPeer := testPipe(t)
	second, secondPeer := testPipe(t)
	audio1 := mustReadTalkAudio(t, testADTS(11, 1, 11))
	audio2 := mustReadTalkAudio(t, testADTS(8, 2, 22))
	results := make(chan error, 2)
	go func() { results <- audio1.send(ctx, &sessionWriter{Conn: first}) }()
	go func() { results <- audio2.send(ctx, &sessionWriter{Conn: second}) }()
	// A stalled write to camera 1 must not block camera 2.
	packet, err := ReadPacket(secondPeer)
	if err != nil || packet.Body[len(packet.Body)-1] != 22 {
		t.Fatalf("second camera packet=%v err=%v", packet, err)
	}
	select {
	case err := <-results:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("second camera blocked by first")
	}
	packet, err = ReadPacket(firstPeer)
	if err != nil || packet.Body[len(packet.Body)-1] != 11 {
		t.Fatalf("first camera packet=%v err=%v", packet, err)
	}
	if err := <-results; err != nil {
		t.Fatal(err)
	}
}

func TestTalkWriteFailure(t *testing.T) {
	client, server := testPipe(t)
	_ = server.Close()
	audio := mustReadTalkAudio(t, testADTS(11, 1, 1))
	if err := audio.send(context.Background(), &sessionWriter{Conn: client}); err == nil || errors.Is(err, io.EOF) {
		t.Fatalf("expected write error, got %v", err)
	}
}
