package netvue

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// attachedTalkback returns a controller bound to a session whose peer drains
// every packet it receives and reports the media ones.
func attachedTalkback(t *testing.T, sessionCtx context.Context) (*LiveTalkback, <-chan Packet) {
	t.Helper()
	client, server := testPipe(t)
	controller := NewLiveTalkback()
	controller.attachIfAbsent(sessionCtx, &sessionWriter{Conn: client}, &audioParameters{sampleRate: 8000, sampleSize: 16, channels: 1})
	media := make(chan Packet, 64)
	go func() {
		for {
			packet, err := ReadPacket(server)
			if err != nil {
				return
			}
			if packet.Kind == packetMedia {
				select {
				case media <- packet:
				default:
				}
			}
		}
	}()
	return controller, media
}

func TestLiveTalkConcurrentOpenGrantsOneStream(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	controller, _ := attachedTalkback(t, ctx)

	const callers = 16
	var wg sync.WaitGroup
	var streams atomic.Int32
	errs := make(chan error, callers)
	for range callers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := controller.Open(ctx); err != nil {
				errs <- err
				return
			}
			streams.Add(1)
		}()
	}
	wg.Wait()
	close(errs)
	if streams.Load() != 1 {
		t.Fatalf("%d streams granted, want exactly 1", streams.Load())
	}
	for err := range errs {
		if !errors.Is(err, ErrTalkBusy) {
			t.Fatalf("losing Open error = %v, want ErrTalkBusy", err)
		}
	}
}

func TestLiveTalkSessionEndRacingWritersAndClose(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	controller, media := attachedTalkback(t, ctx)
	stream, err := controller.Open(ctx)
	if err != nil {
		t.Fatal(err)
	}
	frame := testADTS(11, 1, 7)

	const writers = 4
	results := make(chan error, writers+1)
	for range writers {
		go func() { results <- stream.WriteADTS(ctx, frame) }()
	}
	select {
	case <-media:
	case <-time.After(2 * time.Second):
		t.Fatal("no talkback media reached the camera")
	}
	go func() { results <- stream.Close(ctx) }()
	controller.detach()

	for range writers + 1 {
		select {
		case err := <-results:
			if err != nil && !errors.Is(err, ErrTalkClosed) && !errors.Is(err, ErrTalkback) && !errors.Is(err, context.Canceled) {
				t.Fatalf("unexpected result while the session ended: %v", err)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("talkback operation did not return after the session ended")
		}
	}
	select {
	case <-stream.Done():
	case <-time.After(time.Second):
		t.Fatal("stream was not ended")
	}
	if err := stream.Err(); !errors.Is(err, ErrTalkClosed) {
		t.Fatalf("stream.Err() = %v, want ErrTalkClosed", err)
	}
	if err := stream.WriteADTS(ctx, frame); !errors.Is(err, ErrTalkClosed) {
		t.Fatalf("write after session end = %v, want ErrTalkClosed", err)
	}
	if _, err := controller.Open(ctx); !errors.Is(err, ErrTalkUnavailable) {
		t.Fatalf("Open after session end = %v, want ErrTalkUnavailable", err)
	}
}

func TestLiveTalkControllersAreIsolatedUnderConcurrency(t *testing.T) {
	const cameras = 5
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	controllers := make([]*LiveTalkback, cameras)
	for i := range controllers {
		controllers[i], _ = attachedTalkback(t, ctx)
	}
	streams := make([]*TalkStream, cameras)
	var wg sync.WaitGroup
	for i := range controllers {
		wg.Add(1)
		go func() {
			defer wg.Done()
			stream, err := controllers[i].Open(ctx)
			if err != nil {
				t.Errorf("camera %d Open: %v", i, err)
				return
			}
			streams[i] = stream
		}()
	}
	wg.Wait()
	if t.Failed() {
		return
	}
	// Ending one camera's session must not disturb the others.
	controllers[0].detach()
	for i, stream := range streams[1:] {
		select {
		case <-stream.Done():
			t.Fatalf("camera %d stream ended by another camera", i+1)
		default:
		}
		if err := stream.WriteADTS(ctx, testADTS(11, 1, 1)); err != nil {
			t.Fatalf("camera %d write: %v", i+1, err)
		}
	}
	if err := streams[0].Err(); !errors.Is(err, ErrTalkClosed) {
		t.Fatalf("camera 0 stream error = %v", err)
	}
}

func TestSuperviseManyCamerasStopIndependently(t *testing.T) {
	const cameras = 6
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	policy := retryPolicy{initialDelay: time.Millisecond, maxDelay: 2 * time.Millisecond, stableAfter: time.Hour}
	fatal := fmt.Errorf("camera 0: %w", ErrAuthenticationRejected)
	running := make(chan int, cameras)
	var attempts [cameras]atomic.Int32
	results := make([]chan error, cameras)
	for i := range results {
		results[i] = make(chan error, 1)
		go func() {
			results[i] <- supervise(ctx, fmt.Sprintf("192.0.2.%d", i+10), func(ctx context.Context) error {
				switch {
				case i == 0:
					return fatal
				case attempts[i].Add(1) == 1:
					return ErrStreamInterrupted
				}
				running <- i
				<-ctx.Done()
				return ctx.Err()
			}, policy, waitForRetry)
		}()
	}
	if err := <-results[0]; !errors.Is(err, ErrAuthenticationRejected) {
		t.Fatalf("fatal camera result = %v", err)
	}
	for range cameras - 1 {
		select {
		case <-running:
		case <-time.After(5 * time.Second):
			t.Fatal("a supervised camera did not recover after a retryable failure")
		}
	}
	for i := 1; i < cameras; i++ {
		select {
		case err := <-results[i]:
			t.Fatalf("camera %d stopped early: %v", i, err)
		default:
		}
	}
	cancel()
	for i := 1; i < cameras; i++ {
		select {
		case err := <-results[i]:
			if err != nil {
				t.Fatalf("camera %d returned %v after cancellation", i, err)
			}
		case <-time.After(5 * time.Second):
			t.Fatalf("camera %d did not stop after cancellation", i)
		}
		if got := attempts[i].Load(); got != 2 {
			t.Fatalf("camera %d attempts = %d, want 2", i, got)
		}
	}
}
