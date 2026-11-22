package netvue

import (
	"context"
	"fmt"
	"net"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/bluenviron/gortsplib/v5"
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
	return runContinuousObservedAsync(t, target, rtspURL, nil)
}

func runContinuousObservedAsync(
	t *testing.T, target, rtspURL string, observe func(SessionEvent),
) context.CancelFunc {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- RunContinuousObserved(ctx, "127.0.0.1", target, rtspURL, nil, nil, observe) }()
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

func waitForVideo(t *testing.T, ffmpeg, url string, width, height int, red bool) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	var lastErr error
	for time.Now().Before(deadline) {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		pixel, err := netvuetest.DecodeVideoPixel(ctx, ffmpeg, url, width, height)
		cancel()
		if err == nil && isColor(pixel, red) {
			return
		}
		if err != nil {
			lastErr = err
		} else {
			lastErr = fmt.Errorf("decoded pixel %v does not match the expected fixture", pixel)
		}
		timer := time.NewTimer(100 * time.Millisecond)
		<-timer.C
	}
	t.Fatalf("video %dx%d was not published before the deadline: %v", width, height, lastErr)
}

// Each fault must produce a diagnostic error before RunSession's deadline.
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

func TestReconnectUsesAuthenticatedEndpointWhenDiscoveryRepliesAreLost(t *testing.T) {
	sim := netvuetest.Start(t, netvuetest.Options{Host: netvuetest.AllocHost(simulatorGroup)})
	ctx := simulatorContext(t, 5*time.Second)
	var cached camera
	discoveries := 0
	onDiscovery := func(DiscoveryResult) { discoveries++ }
	conn, _, err := connectCamera(ctx, "127.0.0.1", sim.Host(), &cached, onDiscovery, false)
	if err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
	sim.DropDiscoveryReplies(true)
	conn, _, err = connectCamera(ctx, "127.0.0.1", sim.Host(), &cached, onDiscovery, false)
	if err != nil {
		t.Fatalf("reconnect with cached endpoint: %v", err)
	}
	_ = conn.Close()
	if discoveries != 1 || sim.State().Logins != 2 {
		t.Fatalf("discoveries = %d, logins = %d; want one discovery and two logins",
			discoveries, sim.State().Logins)
	}
}

func TestReconnectRediscoveryAfterCachedAuthenticationRejection(t *testing.T) {
	sim := netvuetest.Start(t, netvuetest.Options{Host: netvuetest.AllocHost(simulatorGroup)})
	ctx := simulatorContext(t, 5*time.Second)
	cached := camera{host: sim.Host(), port: sim.Port(), key: "obsolete"}
	discoveries := 0
	conn, _, err := connectCamera(ctx, "127.0.0.1", sim.Host(), &cached, func(DiscoveryResult) {
		discoveries++
	}, false)
	if err != nil {
		t.Fatalf("rediscover after stale authentication: %v", err)
	}
	_ = conn.Close()
	if discoveries != 1 || cached.key != netvuetest.DefaultKey || sim.State().Logins != 2 {
		t.Fatalf("rediscovery did not replace the obsolete authenticated endpoint")
	}
}

func TestSupervisorRepublishesAfterDisconnectWhenDiscoveryIsLost(t *testing.T) {
	handler := &publisherTestRTSPHandler{
		recorded: make(chan struct{}, 4),
		closed:   make(chan struct{}, 4),
	}
	server := &gortsplib.Server{Handler: handler, RTSPAddress: "127.0.0.1:0"}
	handler.server = server
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)
	url := fmt.Sprintf("rtsp://127.0.0.1:%d/sim", server.NetListener().Addr().(*net.TCPAddr).Port)
	sim := netvuetest.Start(t, netvuetest.Options{Host: netvuetest.AllocHost(simulatorGroup)})
	events := make(chan SessionEvent, 32)
	stop := runContinuousObservedAsync(t, sim.Host(), url, func(event SessionEvent) {
		select {
		case events <- event:
		default:
		}
	})
	ctx := simulatorContext(t, 12*time.Second)
	if err := sim.Wait(ctx, "initial video", func(s netvuetest.Snapshot) bool {
		return s.Streaming == 1 && s.VideoFrames > 0
	}); err != nil {
		t.Fatal(err)
	}
	waitForPublisherRecord(t, handler.recorded, 1)
	sim.DropDiscoveryReplies(true)
	sim.Drop()
	if err := sim.Wait(ctx, "reconnected video", func(s netvuetest.Snapshot) bool {
		return s.Logins >= 2 && s.Streaming == 1
	}); err != nil {
		t.Fatal(err)
	}
	waitForPublisherRecord(t, handler.recorded, 2)
	var firstFrame, failure, resumed bool
	for !resumed {
		select {
		case event := <-events:
			if !event.FrameAt.IsZero() {
				if failure {
					resumed = true
				} else {
					firstFrame = true
				}
			} else if event.Error != "" && firstFrame {
				failure = true
			}
		case <-ctx.Done():
			t.Fatalf("missing media recovery events: first=%v failure=%v resumed=%v", firstFrame, failure, resumed)
		}
	}
	stop()
}

// The supervisor sends discovery, authentication, LIVE, H.264 and AAC to MediaMTX.
func TestSupervisorRediscoveriesAfterCameraRestartChangesPort(t *testing.T) {
	handler := &publisherTestRTSPHandler{
		recorded: make(chan struct{}, 4),
		closed:   make(chan struct{}, 4),
	}
	server := &gortsplib.Server{Handler: handler, RTSPAddress: "127.0.0.1:0"}
	handler.server = server
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)
	url := fmt.Sprintf("rtsp://127.0.0.1:%d/sim", server.NetListener().Addr().(*net.TCPAddr).Port)
	host := netvuetest.AllocHost(simulatorGroup)
	first := netvuetest.Start(t, netvuetest.Options{Host: host})
	firstPort := first.Port()
	reserved, err := net.Listen("tcp4", net.JoinHostPort(host, "0"))
	if err != nil {
		t.Fatal(err)
	}
	newPort := reserved.Addr().(*net.TCPAddr).Port
	if err := reserved.Close(); err != nil {
		t.Fatal(err)
	}
	discoveries := make(chan DiscoveryResult, 4)
	ctx, cancel := context.WithCancel(t.Context())
	done := make(chan error, 1)
	go func() {
		done <- RunContinuousObserved(ctx, "127.0.0.1", host, url, nil,
			func(result DiscoveryResult) { discoveries <- result }, nil)
	}()
	t.Cleanup(func() {
		cancel()
		select {
		case err := <-done:
			if err != nil {
				t.Errorf("supervisor stopped: %v", err)
			}
		case <-time.After(5 * time.Second):
			t.Error("supervisor did not stop")
		}
	})
	waitForPublisherRecord(t, handler.recorded, 1)
	first.Close()
	second := netvuetest.Start(t, netvuetest.Options{Host: host, TCPPort: newPort})
	restartCtx := simulatorContext(t, 12*time.Second)
	if err := second.Wait(restartCtx, "video after restart", func(state netvuetest.Snapshot) bool {
		return state.Logins > 0 && state.Streaming > 0
	}); err != nil {
		t.Fatal(err)
	}
	waitForPublisherRecord(t, handler.recorded, 2)
	if got := <-discoveries; got.ConnectionPort != firstPort {
		t.Fatalf("initial discovery port = %d, want %d", got.ConnectionPort, firstPort)
	}
	select {
	case got := <-discoveries:
		if got.ConnectionPort != second.Port() {
			t.Fatalf("rediscovery port = %d, want %d", got.ConnectionPort, second.Port())
		}
	case <-time.After(time.Second):
		t.Fatal("camera restart did not trigger rediscovery")
	}
}

// Separate FFmpeg readers check reconnect and resolution changes.
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
	waitForVideo(t, ffmpeg, url, 160, 96, true)
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
	waitForVideo(t, ffmpeg, url, 160, 96, true)

	// Resolution change without restarting the reader's source URL.
	camera.SetVideo(netvuetest.SolidH264(320, 192, 0, 0, 255, 10))
	waitForVideo(t, ffmpeg, url, 320, 192, false)

	stop()
	if err := camera.Wait(ctx, "cleanup", func(s netvuetest.Snapshot) bool { return s.Connections == 0 && s.Streaming == 0 }); err != nil {
		t.Fatalf("camera connection leaked after stop: %v", err)
	}
}
