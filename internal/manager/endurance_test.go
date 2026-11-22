package manager

import (
	"context"
	"errors"
	"fmt"
	"runtime"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue"
)

func TestManagerEnduranceIndependentCamerasAndFailure(t *testing.T) {
	const healthyCameras = 8
	var activeMu sync.Mutex
	active := make(map[string]struct{})
	started := make(chan string, healthyCameras+1)

	manager := newTestManager(t, func(ctx context.Context, _, host, _ string) error {
		if host == "192.168.10.40" {
			started <- host
			return errors.New("synthetic session failure")
		}
		activeMu.Lock()
		active[host] = struct{}{}
		activeMu.Unlock()
		started <- host
		<-ctx.Done()
		activeMu.Lock()
		delete(active, host)
		activeMu.Unlock()
		return nil
	})

	for i := 20; i < 20+healthyCameras; i++ {
		host := fmt.Sprintf("192.168.10.%d", i)
		if _, added, err := manager.AddCamera(host, fmt.Sprintf("endurance_%d", i)); err != nil || !added {
			t.Fatalf("AddCamera(%q) = added %v, error %v", host, added, err)
		}
	}
	if _, added, err := manager.AddCamera("192.168.10.40", "failing_camera"); err != nil || !added {
		t.Fatalf("AddCamera(failing camera) = added %v, error %v", added, err)
	}

	for i := 0; i < healthyCameras+1; i++ {
		select {
		case <-started:
		case <-time.After(5 * time.Second):
			t.Fatal("camera session start timed out")
		}
	}
	waitForEnduranceState(t, manager, "192.168.10.40", stateFailed)

	activeMu.Lock()
	if got := len(active); got != healthyCameras {
		activeMu.Unlock()
		t.Fatalf("active healthy sessions = %d, want %d", got, healthyCameras)
	}
	activeMu.Unlock()

	stopCtx, stopCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer stopCancel()
	if _, err := manager.StopCamera(stopCtx, "192.168.10.20"); err != nil {
		t.Fatalf("StopCamera(first independent camera) error = %v", err)
	}
	activeMu.Lock()
	if _, stillActive := active["192.168.10.20"]; stillActive || len(active) != healthyCameras-1 {
		snapshot := fmt.Sprint(active)
		activeMu.Unlock()
		t.Fatalf("stopping one camera affected unexpected sessions: active = %s", snapshot)
	}
	activeMu.Unlock()

	closeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := manager.Close(closeCtx); err != nil {
		t.Fatalf("Manager.Close() error = %v", err)
	}
	activeMu.Lock()
	defer activeMu.Unlock()
	if len(active) != 0 {
		t.Fatalf("active sessions after close = %v", active)
	}
}

func TestManagerEnduranceBoundedLifecycleChurn(t *testing.T) {
	const cycles = 32
	const sustainedSessions = 4
	goroutinesBefore := runtime.NumGoroutine()
	var active, heartbeats atomic.Int32
	started := make(chan struct{}, cycles*2+sustainedSessions)
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		active.Add(1)
		started <- struct{}{}
		ticker := time.NewTicker(20 * time.Millisecond)
		defer ticker.Stop()
		defer active.Add(-1)
		for {
			select {
			case <-ctx.Done():
				return nil
			case <-ticker.C:
				heartbeats.Add(1)
			}
		}
	})
	host, stream := "192.168.10.50", "churn_camera"

	waitStart := func() {
		t.Helper()
		select {
		case <-started:
		case <-time.After(5 * time.Second):
			t.Fatal("churn session start timed out")
		}
	}

	for i := 0; i < cycles; i++ {
		if _, added, err := manager.AddCamera(host, stream); err != nil || !added {
			t.Fatalf("cycle %d AddCamera() = added %v, error %v", i, added, err)
		}
		waitStart()
		stopCtx, stopCancel := context.WithTimeout(context.Background(), 5*time.Second)
		_, stopErr := manager.StopCamera(stopCtx, host)
		stopCancel()
		if stopErr != nil {
			t.Fatalf("cycle %d StopCamera() error = %v", i, stopErr)
		}
		if _, startedAgain, err := manager.StartCamera(host); err != nil || !startedAgain {
			t.Fatalf("cycle %d StartCamera() = started %v, error %v", i, startedAgain, err)
		}
		waitStart()
		deleteCtx, deleteCancel := context.WithTimeout(context.Background(), 5*time.Second)
		deleted, deleteErr := manager.DeleteCamera(deleteCtx, host)
		deleteCancel()
		if deleteErr != nil || !deleted {
			t.Fatalf("cycle %d DeleteCamera() = deleted %v, error %v", i, deleted, deleteErr)
		}
		if _, found := manager.Camera(host); found {
			t.Fatalf("cycle %d camera remains after delete", i)
		}
		if got := active.Load(); got != 0 {
			t.Fatalf("cycle %d active sessions after delete = %d, want 0", i, got)
		}
	}

	for i := 0; i < sustainedSessions; i++ {
		host := fmt.Sprintf("192.168.10.%d", 70+i)
		if _, added, err := manager.AddCamera(host, fmt.Sprintf("sustained_%d", i)); err != nil || !added {
			t.Fatalf("AddCamera(%q) = added %v, error %v", host, added, err)
		}
	}
	for i := 0; i < sustainedSessions; i++ {
		select {
		case <-started:
		case <-time.After(5 * time.Second):
			t.Fatal("sustained sessions did not start")
		}
	}

	heartbeatsBefore := heartbeats.Load()
	window := time.NewTimer(time.Second)
	<-window.C
	if got := active.Load(); got != sustainedSessions {
		t.Fatalf("active sessions after sustained window = %d, want %d", got, sustainedSessions)
	}
	if got := heartbeats.Load() - heartbeatsBefore; got < sustainedSessions {
		t.Fatalf("synthetic heartbeats during one-second window = %d, want at least %d", got, sustainedSessions)
	}

	closeCtx, closeCancel := context.WithTimeout(context.Background(), 5*time.Second)
	if err := manager.Close(closeCtx); err != nil {
		closeCancel()
		t.Fatalf("Manager.Close() after lifecycle churn = %v", err)
	}
	closeCancel()
	if got := active.Load(); got != 0 {
		t.Fatalf("active sessions after close = %d, want 0", got)
	}
	waitForEnduranceGoroutineRecovery(t, goroutinesBefore, 3)
}

func TestManagerEnduranceFiniteConcurrentWorkloadAndCancellation(t *testing.T) {
	const (
		cameraCount = 6
		cycles      = 20
	)
	var active, peak, starts, acceptedStarts atomic.Int32
	started := make(chan struct{}, cameraCount)
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		starts.Add(1)
		current := active.Add(1)
		for observed := peak.Load(); current > observed; observed = peak.Load() {
			if peak.CompareAndSwap(observed, current) {
				break
			}
		}
		select {
		case started <- struct{}{}:
		default:
		}
		<-ctx.Done()
		active.Add(-1)
		return nil
	})

	for i := 0; i < cameraCount; i++ {
		host := fmt.Sprintf("192.168.10.%d", 60+i)
		if _, added, err := manager.AddCamera(host, fmt.Sprintf("workload_%d", i)); err != nil || !added {
			t.Fatalf("AddCamera(%q) = added %v, error %v", host, added, err)
		}
		acceptedStarts.Add(1)
	}
	for i := 0; i < cameraCount; i++ {
		select {
		case <-started:
		case <-time.After(5 * time.Second):
			t.Fatal("initial workload sessions did not start")
		}
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	var workers sync.WaitGroup
	errs := make(chan error, cameraCount)
	for i := 0; i < cameraCount; i++ {
		host := fmt.Sprintf("192.168.10.%d", 60+i)
		workers.Add(1)
		go func() {
			defer workers.Done()
			for cycle := 0; cycle < cycles; cycle++ {
				if _, err := manager.StopCamera(ctx, host); err != nil {
					errs <- fmt.Errorf("stop %s cycle %d: %w", host, cycle, err)
					return
				}
				if _, startedAgain, err := manager.StartCamera(host); err != nil || !startedAgain {
					errs <- fmt.Errorf("start %s cycle %d: started %v, error %v", host, cycle, startedAgain, err)
					return
				}
				acceptedStarts.Add(1)
			}
		}()
	}
	workers.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
	}
	if err := ctx.Err(); err != nil {
		t.Fatalf("workload exceeded its deadline: %v", err)
	}

	closeCtx, closeCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer closeCancel()
	if err := manager.Close(closeCtx); err != nil {
		t.Fatalf("Manager.Close() after workload error = %v", err)
	}
	// Close waits for every launched runner to return. Compare their observed
	// invocations with successful Add/Start results rather than deriving a count
	// from worker scheduling or assuming a particular interleaving.
	if got, want := starts.Load(), acceptedStarts.Load(); got != want {
		t.Errorf("session runner invocations = %d, successful starts = %d", got, want)
	}
	if got := active.Load(); got != 0 {
		t.Errorf("active sessions after cancellation and close = %d, want 0", got)
	}
	if got := peak.Load(); got > cameraCount {
		t.Errorf("peak simultaneous sessions = %d, exceeds %d cameras", got, cameraCount)
	}
}

func BenchmarkManagerEnduranceLifecycle(b *testing.B) {
	manager, err := newManager(context.Background(), "192.168.10.2", func(ctx context.Context, _, _, _ string) error {
		<-ctx.Done()
		return nil
	})
	if err != nil {
		b.Fatal(err)
	}
	b.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := manager.Close(ctx); err != nil {
			b.Errorf("Manager.Close() error = %v", err)
		}
	})

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, added, err := manager.AddCamera("192.168.10.20", "bench_camera"); err != nil || !added {
			b.Fatalf("AddCamera() = added %v, error %v", added, err)
		}
		if _, err := manager.StopCamera(context.Background(), "192.168.10.20"); err != nil {
			b.Fatal(err)
		}
		if _, started, err := manager.StartCamera("192.168.10.20"); err != nil || !started {
			b.Fatalf("StartCamera() = started %v, error %v", started, err)
		}
		if deleted, err := manager.DeleteCamera(context.Background(), "192.168.10.20"); err != nil || !deleted {
			b.Fatalf("DeleteCamera() = deleted %v, error %v", deleted, err)
		}
	}
}

func BenchmarkManagerHealthSnapshot(b *testing.B) {
	manager, err := newManager(context.Background(), "192.168.10.2", func(ctx context.Context, _, _, _ string) error {
		<-ctx.Done()
		return nil
	})
	if err != nil {
		b.Fatal(err)
	}
	b.Cleanup(func() {
		if err := manager.Close(context.Background()); err != nil {
			b.Error(err)
		}
	})
	manager.runObserved = func(ctx context.Context, _, _, _ string, _ *netvue.LiveTalkback, _ func(netvue.SessionEvent)) error {
		<-ctx.Done()
		return nil
	}
	for i := 0; i < 16; i++ {
		host := fmt.Sprintf("192.168.10.%d", 20+i)
		if _, _, err := manager.AddCamera(host, fmt.Sprintf("bench_%d", i)); err != nil {
			b.Fatal(err)
		}
		session := manager.cameras[host]
		manager.observeSession(session, session.done, netvue.SessionEvent{FrameAt: time.Now()})
	}
	b.ReportAllocs()
	b.ResetTimer()
	for b.Loop() {
		if len(manager.Cameras()) != 16 {
			b.Fatal("missing cameras")
		}
	}
}

func BenchmarkManagerEnduranceCameraSnapshot(b *testing.B) {
	manager, err := newManager(context.Background(), "192.168.10.2", func(ctx context.Context, _, _, _ string) error {
		<-ctx.Done()
		return nil
	})
	if err != nil {
		b.Fatal(err)
	}
	b.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := manager.Close(ctx); err != nil {
			b.Errorf("Manager.Close() error = %v", err)
		}
	})
	for i := 0; i < 16; i++ {
		host := fmt.Sprintf("192.168.10.%d", 20+i)
		if _, added, err := manager.AddCamera(host, fmt.Sprintf("bench_%d", i)); err != nil || !added {
			b.Fatalf("AddCamera(%q) = added %v, error %v", host, added, err)
		}
	}

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if got := len(manager.Cameras()); got != 16 {
			b.Fatalf("Cameras() length = %d, want 16", got)
		}
	}
}

func waitForEnduranceState(t *testing.T, manager *Manager, host, want string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	if testDeadline, ok := t.Deadline(); ok && testDeadline.Before(deadline) {
		deadline = testDeadline
	}
	ticker := time.NewTicker(5 * time.Millisecond)
	defer ticker.Stop()
	timeout := time.NewTimer(time.Until(deadline))
	defer timeout.Stop()
	for {
		if camera, found := manager.Camera(host); found && camera.State == want {
			return
		}
		select {
		case <-ticker.C:
		case <-timeout.C:
			camera, _ := manager.Camera(host)
			t.Fatalf("camera %s state = %q, want %q", host, camera.State, want)
		}
	}
}

func waitForEnduranceGoroutineRecovery(t *testing.T, baseline, tolerance int) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	if testDeadline, ok := t.Deadline(); ok && testDeadline.Before(deadline) {
		deadline = testDeadline
	}
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	timeout := time.NewTimer(time.Until(deadline))
	defer timeout.Stop()

	const stableSamples = 3
	stable := 0
	for {
		current := runtime.NumGoroutine()
		if current <= baseline+tolerance {
			stable++
			if stable == stableSamples {
				t.Logf("goroutines after close: %d (baseline %d, tolerance +%d)", current, baseline, tolerance)
				return
			}
		} else {
			stable = 0
		}
		select {
		case <-ticker.C:
		case <-timeout.C:
			t.Fatalf("goroutines did not return near baseline: current %d, baseline %d, tolerance +%d",
				runtime.NumGoroutine(), baseline, tolerance)
		}
	}
}
