package netvue

import (
	"context"
	"fmt"
	"net"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue/netvuetest"
)

// Loopback group of this package's simulated cameras (see netvuetest.AllocHost).
const simulatorGroup = 20

func simulatorContext(t *testing.T, limit time.Duration) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), limit)
	t.Cleanup(cancel)
	return ctx
}

// runContinuousAsync runs the production supervisor against a camera and
// stops it, waiting for its exit, at cleanup.
func runContinuousAsync(t *testing.T, target, rtspURL string) context.CancelFunc {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- RunContinuous(ctx, "127.0.0.1", target, rtspURL) }()
	var once sync.Once
	stop := func() {
		once.Do(func() {
			cancel()
			select {
			case <-done:
			case <-time.After(15 * time.Second):
				t.Error("RunContinuous did not stop after cancellation")
			}
		})
	}
	t.Cleanup(stop)
	return stop
}

func captureVideo(t *testing.T, ffmpeg, url string, width, height int) [3]byte {
	t.Helper()
	ctx, cancel := netvuetest.Within(30 * time.Second)
	defer cancel()
	pixel, err := netvuetest.DecodeVideoPixel(ctx, ffmpeg, url, width, height)
	if err != nil {
		t.Fatal(err)
	}
	return pixel
}

func captureAudioPeak(t *testing.T, ffmpeg, url string) int {
	t.Helper()
	ctx, cancel := netvuetest.Within(30 * time.Second)
	defer cancel()
	peak, err := netvuetest.DecodeAudioPeak(ctx, ffmpeg, url)
	if err != nil {
		t.Fatal(err)
	}
	return peak
}

func isColor(pixel [3]byte, red bool) bool {
	if red {
		return netvuetest.IsRed(pixel)
	}
	return netvuetest.IsBlue(pixel)
}
// Every protocol failure returned by the simulator must stop RunSession
// with a protocol error instead of hanging or succeeding.
func TestSimulatorFaultsStopRunSession(t *testing.T) {
	faults := []struct{ stage, fault string }{
		{"login", "close-connection"},
		{"login", "flagged-header"},
		{"login", "wrong-packet-kind"},
		{"login", "invalid-json"},
		{"login", "missing-result"},
		{"login", "unsupported-version"},
		{"live", "invalid-json"},
		{"live", "missing-result"},
		{"live", "rejected-result"},
	}
	for _, f := range faults {
		t.Run(f.stage+"/"+f.fault, func(t *testing.T) {
			camera := netvuetest.Start(t, netvuetest.Options{Host: netvuetest.AllocHost(simulatorGroup)})
			if err := camera.FailNext(f.stage, f.fault); err != nil {
				t.Fatal(err)
			}
			listener, err := net.Listen("tcp4", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			defer listener.Close()
			rtspURL := fmt.Sprintf("rtsp://127.0.0.1:%d/netvue", listener.Addr().(*net.TCPAddr).Port)
			ctx := simulatorContext(t, 10*time.Second)
			err = RunSession(ctx, "127.0.0.1", camera.Host(), rtspURL, 5*time.Second)
			if err == nil {
				t.Fatal("RunSession succeeded despite the injected camera fault")
			}
			if ctx.Err() != nil {
				t.Fatalf("RunSession hung until the deadline: %v", err)
			}
			if got := camera.State().LastFault[f.stage]; got != f.fault {
				t.Fatalf("fault not applied: last %q (error: %v)", got, err)
			}
			if SessionDiagnostic(err) == "" {
				t.Fatalf("no diagnostic for %v", err)
			}
		})
	}
}

// Discovery, authentication, LIVE selection, H.264 and AAC through the real
// supervisor into MediaMTX, then a dropped connection and a resolution change
// while an external reader keeps decoding.
func TestSimulatorPublishesDecodableAudioVideoAcrossReconnects(t *testing.T) {
	mediamtx := netvuetest.Executable(t, "NETVUE_TEST_MEDIAMTX", "mediamtx")
	ffmpeg := netvuetest.Executable(t, "NETVUE_TEST_FFMPEG", "ffmpeg")
	port := startMediaMTX(t, mediamtx, filepath.Join(t.TempDir(), "mediamtx.yml"))
	url := fmt.Sprintf("rtsp://127.0.0.1:%d/sim", port)

	tone, err := netvuetest.ToneADTS(ffmpeg, 8000, 440, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	camera := netvuetest.Start(t, netvuetest.Options{
		Host: netvuetest.AllocHost(simulatorGroup), Audio: true, AudioFrames: tone,
		Video: netvuetest.SolidH264(160, 96, 255, 0, 0, 10),
	})
	stop := runContinuousAsync(t, camera.Host(), url)

	ctx := simulatorContext(t, 60*time.Second)
	streaming := func(s netvuetest.Snapshot) bool { return s.Streaming == 1 && s.VideoFrames >= 5 }
	if err := camera.Wait(ctx, "first stream", streaming); err != nil {
		t.Fatal(err)
	}
	if s := camera.State(); s.Logins < 1 || s.LiveRequests < 1 {
		t.Fatalf("camera did not authenticate and receive a LIVE request: %+v", s)
	}
	if !isColor(captureVideo(t, ffmpeg, url, 160, 96), true) {
		t.Fatal("decoded video is not the red fixture")
	}
	if peak := captureAudioPeak(t, ffmpeg, url); peak < 1000 {
		t.Fatalf("decoded audio is silent (peak %d)", peak)
	}

	// Dropped connection: the supervisor reconnects and publishing resumes.
	before := camera.State()
	camera.Drop()
	if err := camera.Wait(ctx, "reconnection", func(s netvuetest.Snapshot) bool {
		return s.LiveRequests > before.LiveRequests && s.Streaming == 1
	}); err != nil {
		t.Fatal(err)
	}
	if !isColor(captureVideo(t, ffmpeg, url, 160, 96), true) {
		t.Fatal("video did not recover after the reconnection")
	}

	// Resolution change without restarting the reader's source URL.
	camera.SetVideo(netvuetest.SolidH264(320, 192, 0, 0, 255, 10))
	if !isColor(captureVideo(t, ffmpeg, url, 320, 192), false) {
		t.Fatal("blue 320x192 video was not published after the resolution change")
	}

	stop()
	if err := camera.Wait(ctx, "cleanup", func(s netvuetest.Snapshot) bool { return s.Connections == 0 && s.Streaming == 0 }); err != nil {
		t.Fatalf("camera connection leaked after stop: %v", err)
	}
}
