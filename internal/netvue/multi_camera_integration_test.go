package netvue

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

// Each publisher and decoder has exactly one owner; cleanup also waits for it.
type multiCameraPublisher struct {
	cancel context.CancelFunc
	done   chan error
}

func startMultiCameraPublisher(t *testing.T, url string, frames [][]byte) *multiCameraPublisher {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	publisher, err := newVideoPublisher(ctx, url)
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	p := &multiCameraPublisher{cancel: cancel, done: make(chan error, 1)}
	ready := make(chan struct{})
	go func() {
		var result error
		defer func() {
			publisher.Close()
			p.done <- result
		}()
		ticker := time.NewTicker(100 * time.Millisecond)
		defer ticker.Stop()
		for i := 0; ; i++ {
			if err := publisher.Push(frames[i%len(frames)], uint32(i*100)); err != nil {
				if ctx.Err() == nil {
					result = err
				}
				return
			}
			if i == 0 {
				close(ready)
			}
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
		}
	}()
	t.Cleanup(func() { p.stop(t) })
	select {
	case <-ready:
	case err := <-p.done:
		p.done = nil
		t.Fatalf("start publisher %s: %v", url, err)
	case <-time.After(10 * time.Second):
		t.Fatalf("publisher %s did not start", url)
	}
	return p
}

func (p *multiCameraPublisher) stop(t *testing.T) {
	t.Helper()
	if p.done == nil {
		return
	}
	p.cancel()
	select {
	case err := <-p.done:
		p.done = nil
		if err != nil {
			t.Errorf("publisher failed: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Error("publisher did not stop")
	}
}

// A sample's timestamp is taken when FFmpeg actually emitted it, not when
// the test consumes the channel, so buffered frames cannot prove continuity.
type multiCameraSample struct {
	at    time.Time
	pixel [3]byte
}

type multiCameraDecoder struct {
	cancel  context.CancelFunc
	samples chan multiCameraSample
	done    chan error
}

func startMultiCameraDecoder(t *testing.T, ffmpeg, url string, width, height int) *multiCameraDecoder {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	// Reject frames of the wrong resolution before scaling for colour checks.
	filter := fmt.Sprintf(`select=eq(iw\,%d)*eq(ih\,%d),scale=1:1`, width, height)
	command := exec.CommandContext(ctx, ffmpeg, "-hide_banner", "-loglevel", "error",
		"-rtsp_transport", "tcp", "-i", url, "-an", "-vf", filter,
		"-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1")
	var stderr bytes.Buffer
	command.Stderr = &stderr
	stdout, err := command.StdoutPipe()
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	if err := command.Start(); err != nil {
		cancel()
		t.Fatalf("start FFmpeg reader: %v", err)
	}
	d := &multiCameraDecoder{
		cancel: cancel, samples: make(chan multiCameraSample, 512), done: make(chan error, 1),
	}
	go func() {
		for {
			var pixel [3]byte
			if _, err := io.ReadFull(stdout, pixel[:]); err != nil {
				break
			}
			select {
			case d.samples <- multiCameraSample{at: time.Now(), pixel: pixel}:
			case <-ctx.Done():
			}
		}
		err := command.Wait()
		d.done <- fmt.Errorf("FFmpeg reader exited: %v; stderr: %s", err, stderr.String())
	}()
	t.Cleanup(func() {
		cancel()
		select {
		case <-d.done:
		case <-time.After(10 * time.Second):
			t.Error("FFmpeg reader did not stop")
		}
	})
	return d
}

func (d *multiCameraDecoder) expect(t *testing.T, after time.Time, red bool) {
	t.Helper()
	timer := time.NewTimer(15 * time.Second)
	defer timer.Stop()
	var first time.Time
	for count := 0; ; {
		select {
		case sample := <-d.samples:
			if sample.at.Before(after) {
				continue
			}
			p := sample.pixel
			if (red && (p[0] < 200 || p[1] > 45 || p[2] > 45)) ||
				(!red && (p[2] < 200 || p[0] > 45 || p[1] > 45)) {
				t.Fatalf("unexpected decoded colour RGB=%v (red=%v)", p, red)
			}
			if first.IsZero() {
				first = sample.at
			}
			count++
			if count >= 5 && sample.at.Sub(first) >= 500*time.Millisecond {
				return
			}
		case err := <-d.done:
			// Preserve the result for cleanup.
			d.done <- err
			t.Fatal(err)
		case <-timer.C:
			t.Fatalf("timed out waiting for sustained decoded frames (red=%v)", red)
		}
	}
}

func multiCameraFrames(t *testing.T, ffmpeg, path, color, size string) [][]byte {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, ffmpeg, "-hide_banner", "-loglevel", "error",
		"-f", "lavfi", "-i", fmt.Sprintf("color=c=%s:size=%s:rate=10", color, size),
		"-frames:v", "20", "-pix_fmt", "yuv420p", "-c:v", "libx264",
		"-preset", "ultrafast", "-tune", "zerolatency",
		"-x264-params", "keyint=10:min-keyint=10:repeat-headers=1",
		"-f", "mpegts", path)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("generate %s fixture: %v; output: %s", color, err, output)
	}
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	var frames [][]byte
	if err := readH264Frames(file, func(_ int64, accessUnit []byte) error {
		frames = append(frames, accessUnit)
		return nil
	}); err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	if len(frames) != 20 {
		t.Fatalf("fixture has %d frames, want 20", len(frames))
	}
	return frames
}

func TestMediaMTXMultiCameraIsolation(t *testing.T) {
	mediamtx, ffmpeg := os.Getenv("NETVUE_TEST_MEDIAMTX"), os.Getenv("NETVUE_TEST_FFMPEG")
	if mediamtx == "" || ffmpeg == "" {
		t.Skip("set NETVUE_TEST_MEDIAMTX and NETVUE_TEST_FFMPEG to run the loopback integration test")
	}
	for _, binary := range []string{mediamtx, ffmpeg} {
		if _, err := exec.LookPath(binary); err != nil {
			t.Skipf("integration binary unavailable: %v", err)
		}
	}
	dir := t.TempDir()
	red := multiCameraFrames(t, ffmpeg, filepath.Join(dir, "red.ts"), "red", "160x90")
	blue := multiCameraFrames(t, ffmpeg, filepath.Join(dir, "blue.ts"), "blue", "160x90")
	redLarge := multiCameraFrames(t, ffmpeg, filepath.Join(dir, "red-large.ts"), "red", "320x180")
	port := startMediaMTX(t, mediamtx, filepath.Join(dir, "mediamtx.yml"))
	urlA := fmt.Sprintf("rtsp://127.0.0.1:%d/camera-a", port)
	urlB := fmt.Sprintf("rtsp://127.0.0.1:%d/camera-b", port)

	a := startMultiCameraPublisher(t, urlA, red)
	b := startMultiCameraPublisher(t, urlB, blue)
	readerA := startMultiCameraDecoder(t, ffmpeg, urlA, 160, 90)
	readerB := startMultiCameraDecoder(t, ffmpeg, urlB, 160, 90)
	readerA.expect(t, time.Time{}, true)
	readerB.expect(t, time.Time{}, false)

	a.stop(t)
	readerB.expect(t, time.Now(), false)
	a = startMultiCameraPublisher(t, urlA, redLarge)
	readerA2 := startMultiCameraDecoder(t, ffmpeg, urlA, 320, 180)
	readerA2.expect(t, time.Time{}, true)
	readerB.expect(t, time.Now(), false)
	b.stop(t)
	readerA2.expect(t, time.Now(), true)
	a.stop(t)
}
