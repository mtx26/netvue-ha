package manager

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/mtx26/netvue-copilot-phase1/internal/netvue"
)

func TestManagerRunsFiveIndependentCameras(t *testing.T) {
	started := make(chan string, 5)
	manager := newTestManager(t, func(ctx context.Context, _, host, _ string) error {
		started <- host
		<-ctx.Done()
		return nil
	})

	for i := 10; i < 15; i++ {
		host := fmt.Sprintf("192.168.10.%d", i)
		stream := fmt.Sprintf("camera_%d", i)
		camera, added, err := manager.AddCamera(host, stream)
		if err != nil || !added {
			t.Fatalf("AddCamera(%q) = %v, %v", host, added, err)
		}
		if camera.RTSPURL != "rtsp://127.0.0.1:8554/"+stream {
			t.Fatalf("camera RTSP URL = %q", camera.RTSPURL)
		}
	}
	for range 5 {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("camera session did not start")
		}
	}

	cameras := manager.Cameras()
	if len(cameras) != 5 {
		t.Fatalf("camera count = %d, want 5", len(cameras))
	}
	for i := 1; i < len(cameras); i++ {
		if cameras[i-1].Host >= cameras[i].Host {
			t.Fatalf("camera list is not sorted: %q >= %q", cameras[i-1].Host, cameras[i].Host)
		}
	}
	if _, added, err := manager.AddCamera("192.168.10.10", "camera_10"); err != nil || added {
		t.Fatalf("idempotent AddCamera() = added %v, error %v", added, err)
	}
	if _, _, err := manager.AddCamera("192.168.10.10", "different"); !errors.Is(err, ErrCameraConflict) {
		t.Fatalf("same host with another stream error = %v", err)
	}
	if _, _, err := manager.AddCamera("192.168.10.20", "camera_10"); !errors.Is(err, ErrCameraConflict) {
		t.Fatalf("duplicate stream error = %v", err)
	}
}

func TestManagerConcurrentAddStartsOneSession(t *testing.T) {
	var starts atomic.Int32
	started := make(chan struct{}, 1)
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		starts.Add(1)
		started <- struct{}{}
		<-ctx.Done()
		return nil
	})

	const attempts = 20
	var workers sync.WaitGroup
	var added atomic.Int32
	for range attempts {
		workers.Add(1)
		go func() {
			defer workers.Done()
			_, wasAdded, err := manager.AddCamera("192.168.10.20", "one_camera")
			if err != nil {
				t.Errorf("AddCamera() error = %v", err)
				return
			}
			if wasAdded {
				added.Add(1)
			}
		}()
	}
	workers.Wait()
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("camera session did not start")
	}
	if got := starts.Load(); got != 1 {
		t.Fatalf("session starts = %d, want 1", got)
	}
	if got := added.Load(); got != 1 {
		t.Fatalf("successful additions = %d, want 1", got)
	}
}

func TestManagerStopDeleteAndRestart(t *testing.T) {
	var starts atomic.Int32
	started := make(chan int32, 2)
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		count := starts.Add(1)
		started <- count
		<-ctx.Done()
		return nil
	})

	if _, _, err := manager.AddCamera("192.168.10.20", "one_camera"); err != nil {
		t.Fatal(err)
	}
	waitForStart(t, started, 1)
	stopped, err := manager.StopCamera(context.Background(), "192.168.10.20")
	if err != nil || stopped.State != stateStopped {
		t.Fatalf("StopCamera() = %+v, %v", stopped, err)
	}

	if _, startedAgain, err := manager.StartCamera("192.168.10.20"); err != nil || !startedAgain {
		t.Fatalf("StartCamera() = started %v, error %v", startedAgain, err)
	}
	waitForStart(t, started, 2)
	if deleted, err := manager.DeleteCamera(context.Background(), "192.168.10.20"); err != nil || !deleted {
		t.Fatalf("DeleteCamera() = deleted %v, error %v", deleted, err)
	}
	if _, found := manager.Camera("192.168.10.20"); found {
		t.Fatal("camera remains after deletion")
	}
}

func TestManagerStoppingOneCameraLeavesOtherRunning(t *testing.T) {
	active := make(map[string]bool)
	var mu sync.Mutex
	started := make(chan string, 2)
	manager := newTestManager(t, func(ctx context.Context, _, host, _ string) error {
		mu.Lock()
		active[host] = true
		mu.Unlock()
		started <- host
		<-ctx.Done()
		mu.Lock()
		delete(active, host)
		mu.Unlock()
		return nil
	})

	for i := 20; i < 22; i++ {
		host := fmt.Sprintf("192.168.10.%d", i)
		if _, _, err := manager.AddCamera(host, fmt.Sprintf("camera_%d", i)); err != nil {
			t.Fatal(err)
		}
	}
	for range 2 {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("camera session did not start")
		}
	}
	if _, err := manager.StopCamera(context.Background(), "192.168.10.20"); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	firstActive, secondActive := active["192.168.10.20"], active["192.168.10.21"]
	mu.Unlock()
	if firstActive || !secondActive {
		t.Fatalf("active cameras after stopping first = first:%v second:%v", firstActive, secondActive)
	}
}

func TestDeleteSerializesWithConcurrentRestart(t *testing.T) {
	stopping := make(chan struct{}, 1)
	release := make(chan struct{})
	var starts atomic.Int32
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		if starts.Add(1) == 1 {
			<-ctx.Done()
			stopping <- struct{}{}
			<-release
			return nil
		}
		<-ctx.Done()
		return nil
	})
	if _, _, err := manager.AddCamera("192.168.10.20", "one_camera"); err != nil {
		t.Fatal(err)
	}

	type deleteResult struct {
		deleted bool
		err     error
	}
	deleted := make(chan deleteResult, 1)
	go func() {
		ok, err := manager.DeleteCamera(context.Background(), "192.168.10.20")
		deleted <- deleteResult{deleted: ok, err: err}
	}()
	select {
	case <-stopping:
	case <-time.After(time.Second):
		t.Fatal("camera session was not canceled")
	}

	restarted := make(chan error, 1)
	go func() {
		_, _, err := manager.StartCamera("192.168.10.20")
		restarted <- err
	}()
	close(release)

	select {
	case result := <-deleted:
		if result.err != nil || !result.deleted {
			t.Fatalf("DeleteCamera() = %v, %v", result.deleted, result.err)
		}
	case <-time.After(time.Second):
		t.Fatal("camera deletion did not finish")
	}
	select {
	case err := <-restarted:
		if !errors.Is(err, ErrCameraNotFound) {
			t.Fatalf("StartCamera() after delete error = %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("concurrent start did not finish")
	}
	if got := starts.Load(); got != 1 {
		t.Fatalf("session starts = %d, want 1", got)
	}
	if _, found := manager.Camera("192.168.10.20"); found {
		t.Fatal("deleted camera remains registered")
	}
}

func TestManagerFatalSessionCanBeRestartedExplicitly(t *testing.T) {
	var starts atomic.Int32
	started := make(chan struct{}, 1)
	secondStarted := make(chan struct{}, 1)
	manager := newTestManager(t, func(ctx context.Context, _, host, _ string) error {
		if host == "192.168.10.21" {
			secondStarted <- struct{}{}
			<-ctx.Done()
			return nil
		}
		if starts.Add(1) == 1 {
			return fmt.Errorf("%w: sensitive detail", netvue.ErrAuthenticationRejected)
		}
		started <- struct{}{}
		<-ctx.Done()
		return nil
	})

	if _, _, err := manager.AddCamera("192.168.10.20", "one_camera"); err != nil {
		t.Fatal(err)
	}
	if _, _, err := manager.AddCamera("192.168.10.21", "second_camera"); err != nil {
		t.Fatal(err)
	}
	waitForSignal(t, secondStarted, "second camera session")
	waitForState(t, manager, "192.168.10.20", stateFailed)
	failed, found := manager.Camera("192.168.10.20")
	if !found || failed.Error != "Camera authentication rejected" {
		t.Fatalf("failed camera diagnostic = %+v, found %v", failed, found)
	}
	get := request(t, manager.Handler(), http.MethodGet, "/api/v1/cameras/192.168.10.20", "", http.StatusOK)
	if strings.Contains(get.Body.String(), "sensitive detail") {
		t.Fatalf("camera response contains sensitive detail: %s", get.Body.String())
	}
	if _, added, err := manager.AddCamera("192.168.10.20", "one_camera"); err != nil || added {
		t.Fatalf("duplicate add restarted failed camera: added %v, error %v", added, err)
	}
	start := request(t, manager.Handler(), http.MethodPost, "/api/v1/cameras/192.168.10.20/start", "", http.StatusAccepted)
	var camera Camera
	if err := json.Unmarshal(start.Body.Bytes(), &camera); err != nil || camera.Error != "" || camera.State != stateSupervising {
		t.Fatalf("restart response = %+v, error %v", camera, err)
	}
	waitForSignal(t, started, "explicit restart")
	other, found := manager.Camera("192.168.10.21")
	if !found || other.State != stateSupervising {
		t.Fatalf("other camera after restart = %+v, found %v", other, found)
	}
	select {
	case <-secondStarted:
		t.Fatal("restarting the first camera restarted the second")
	default:
	}
}

func TestSessionErrorDiagnosticsDoNotExposeUnderlyingErrors(t *testing.T) {
	tests := []struct {
		err  error
		want string
	}{
		{fmt.Errorf("%w: secret", netvue.ErrAuthenticationRejected), "Camera authentication rejected"},
		{fmt.Errorf("%w: secret", netvue.ErrLiveResponseVersion), "Unsupported Netvue LIVE response version"},
		{fmt.Errorf("%w: secret", netvue.ErrProtocol), "Invalid Netvue protocol data"},
		{fmt.Errorf("%w: secret", netvue.ErrMediaFrame), "Invalid Netvue media frame"},
		{fmt.Errorf("%w: secret", netvue.ErrConnectionClosed), "Connection error"},
		{fmt.Errorf("%w: secret", netvue.ErrVideoFatal), "Video publication failed"},
		{errors.New("secret"), "Unexpected camera session error"},
	}
	for _, test := range tests {
		if got := netvue.SessionDiagnostic(test.err); got != test.want {
			t.Errorf("SessionDiagnostic() = %q, want %q", got, test.want)
		}
	}
}

func TestFailedSessionDoesNotLogSensitiveError(t *testing.T) {
	var logs bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	defer slog.SetDefault(previous)

	manager := newTestManager(t, func(context.Context, string, string, string) error {
		return errors.New("sensitive detail")
	})
	if _, _, err := manager.AddCamera("192.168.10.20", "one_camera"); err != nil {
		t.Fatal(err)
	}
	waitForState(t, manager, "192.168.10.20", stateFailed)
	camera, found := manager.Camera("192.168.10.20")
	if !found || camera.Error != "Unexpected camera session error" ||
		strings.Contains(logs.String(), "sensitive detail") {
		t.Fatalf("unsafe session diagnostic or log: camera=%+v log=%s", camera, logs.String())
	}
}

func TestFailedSessionExposesSafeProtocolReason(t *testing.T) {
	var logs bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	defer slog.SetDefault(previous)

	manager := newTestManager(t, func(context.Context, string, string, string) error {
		_, err := netvue.ParseUnits([]byte("secret-payload"))
		return err
	})
	if _, _, err := manager.AddCamera("192.168.10.20", "one_camera"); err != nil {
		t.Fatal(err)
	}
	waitForState(t, manager, "192.168.10.20", stateFailed)
	response := request(t, manager.Handler(), http.MethodGet, "/api/v1/cameras/192.168.10.20", "", http.StatusOK)
	for _, output := range []string{response.Body.String(), logs.String()} {
		if !strings.Contains(output, "invalid command JSON") || strings.Contains(output, "secret-payload") {
			t.Fatalf("unsafe or missing diagnostic: %s", output)
		}
	}
}

func TestManagerCloseStopsSessionsAndRejectsAdds(t *testing.T) {
	started := make(chan struct{}, 2)
	var active atomic.Int32
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		active.Add(1)
		started <- struct{}{}
		<-ctx.Done()
		active.Add(-1)
		return nil
	})
	for i := 20; i < 22; i++ {
		if _, _, err := manager.AddCamera(
			fmt.Sprintf("192.168.10.%d", i),
			fmt.Sprintf("camera_%d", i),
		); err != nil {
			t.Fatal(err)
		}
	}
	for range 2 {
		select {
		case <-started:
		case <-time.After(time.Second):
			t.Fatal("camera session did not start")
		}
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := manager.Close(ctx); err != nil {
		t.Fatalf("Close() error = %v", err)
	}
	if got := active.Load(); got != 0 {
		t.Fatalf("active sessions after close = %d", got)
	}
	if _, _, err := manager.AddCamera("192.168.10.30", "late_camera"); !errors.Is(err, ErrManagerClosed) {
		t.Fatalf("AddCamera() after close error = %v", err)
	}
}

func TestManagerHTTPRoutesReconcileAndDeleteCameras(t *testing.T) {
	var starts atomic.Int32
	started := make(chan struct{}, 1)
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		starts.Add(1)
		started <- struct{}{}
		<-ctx.Done()
		return nil
	})
	handler := manager.Handler()
	requestBody := `{"host":"192.168.10.20","stream_name":"living_room"}`

	addResult := request(t, handler, http.MethodPost, "/api/v1/cameras", requestBody, http.StatusAccepted)
	var response Camera
	if err := json.Unmarshal(addResult.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if response.RTSPURL != "rtsp://127.0.0.1:8554/living_room" || response.Host != "192.168.10.20" {
		t.Fatalf("POST camera response = %+v", response)
	}
	waitForSignal(t, started, "camera session")

	request(t, handler, http.MethodPost, "/api/v1/cameras", requestBody, http.StatusOK)
	if starts.Load() != 1 {
		t.Fatalf("duplicate POST session starts = %d", starts.Load())
	}

	request(t, handler, http.MethodGet, "/api/v1/cameras/192.168.10.20", "", http.StatusOK)
	status := request(t, handler, http.MethodGet, "/api/v1/status", "", http.StatusOK)
	if !strings.Contains(status.Body.String(), "living_room") {
		t.Fatalf("status response = %s", status.Body.String())
	}

	request(t, handler, http.MethodDelete, "/api/v1/cameras/192.168.10.20", "", http.StatusOK)
	request(t, handler, http.MethodGet, "/api/v1/cameras/192.168.10.20", "", http.StatusNotFound)
}

func TestManagerHTTPRejectsInvalidRequestsAndReportsMissingDelete(t *testing.T) {
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		<-ctx.Done()
		return nil
	})
	handler := manager.Handler()
	for _, test := range []struct {
		name string
		body string
	}{
		{"invalid stream", `{"host":"192.168.10.20","stream_name":"../invalid"}`},
		{"unknown field", `{"host":"192.168.10.20","stream_name":"valid","extra":true}`},
		{"trailing JSON", `{"host":"192.168.10.20","stream_name":"valid"} {"extra":true}`},
	} {
		t.Run(test.name, func(t *testing.T) {
			request(t, handler, http.MethodPost, "/api/v1/cameras", test.body, http.StatusBadRequest)
		})
	}
	request(t, handler, http.MethodDelete, "/api/v1/cameras/192.168.10.20", "", http.StatusNotFound)
}

func TestManagerHTTPStopsAndRestartsCamera(t *testing.T) {
	var starts atomic.Int32
	started := make(chan struct{}, 2)
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		starts.Add(1)
		started <- struct{}{}
		<-ctx.Done()
		return nil
	})
	handler := manager.Handler()
	request(t, handler, http.MethodPost, "/api/v1/cameras",
		`{"host":"192.168.10.20","stream_name":"living_room"}`, http.StatusAccepted)
	waitForSignal(t, started, "camera session")

	request(t, handler, http.MethodPost, "/api/v1/cameras/192.168.10.20/stop", "", http.StatusOK)
	if camera, found := manager.Camera("192.168.10.20"); !found || camera.State != stateStopped {
		t.Fatalf("camera after stop = %+v, found = %v", camera, found)
	}

	request(t, handler, http.MethodPost, "/api/v1/cameras/192.168.10.20/start", "", http.StatusAccepted)
	waitForSignal(t, started, "camera restart")
	if got := starts.Load(); got != 2 {
		t.Fatalf("session starts = %d, want 2", got)
	}
}

func TestManagerHasNoPersistentCameraState(t *testing.T) {
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		<-ctx.Done()
		return nil
	})
	if got := manager.Cameras(); len(got) != 0 {
		t.Fatalf("new manager cameras = %v", got)
	}
}

func TestControlAPIOnlyAcceptsLoopbackListenAddresses(t *testing.T) {
	for _, address := range []string{"127.0.0.1:8765", "[::1]:8765"} {
		if err := validateLoopbackAddress(address); err != nil {
			t.Errorf("validateLoopbackAddress(%q) error = %v", address, err)
		}
	}
	for _, address := range []string{"0.0.0.0:8765", "192.168.10.2:8765", "localhost:8765"} {
		if err := validateLoopbackAddress(address); err == nil {
			t.Errorf("validateLoopbackAddress(%q) accepted a non-loopback address", address)
		}
	}
}

func TestRunStopsControlAPIWhenContextIsCanceled(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan error, 1)
	go func() {
		result <- Run(ctx, "192.168.10.2", address)
	}()

	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		conn, err := net.DialTimeout("tcp", address, 10*time.Millisecond)
		if err == nil {
			_ = conn.Close()
			cancel()
			select {
			case err := <-result:
				if err != nil {
					t.Fatalf("Run() shutdown error = %v", err)
				}
				return
			case <-time.After(time.Second):
				t.Fatal("Run() did not stop after cancellation")
			}
		}
		time.Sleep(time.Millisecond)
	}
	cancel()
	t.Fatal("control API did not start")
}

func newTestManager(t *testing.T, runner sessionRunner) *Manager {
	t.Helper()
	manager, err := newManager(context.Background(), "192.168.10.2", runner)
	if err != nil {
		t.Fatalf("newManager() error = %v", err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		if err := manager.Close(ctx); err != nil {
			t.Errorf("Manager.Close() error = %v", err)
		}
	})
	return manager
}

func request(t *testing.T, handler http.Handler, method, path, body string, want int) *httptest.ResponseRecorder {
	t.Helper()
	result := httptest.NewRecorder()
	handler.ServeHTTP(result, httptest.NewRequest(method, path, strings.NewReader(body)))
	if result.Code != want {
		t.Fatalf("%s %s: status = %d, want %d, body = %s", method, path, result.Code, want, result.Body.String())
	}
	return result
}

func waitForSignal(t *testing.T, started <-chan struct{}, description string) {
	t.Helper()
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatalf("%s did not start", description)
	}
}

func waitForStart(t *testing.T, started <-chan int32, want int32) {
	t.Helper()
	select {
	case got := <-started:
		if got != want {
			t.Fatalf("session start sequence = %d, want %d", got, want)
		}
	case <-time.After(time.Second):
		t.Fatalf("session %d did not start", want)
	}
}

func waitForState(t *testing.T, manager *Manager, host, state string) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		camera, found := manager.Camera(host)
		if found && camera.State == state {
			return
		}
		time.Sleep(time.Millisecond)
	}
	camera, _ := manager.Camera(host)
	t.Fatalf("camera state = %q, want %q", camera.State, state)
}
