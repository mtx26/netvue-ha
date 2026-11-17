package netvue

import (
	"context"
	"errors"
	"io"
	"net"
	"net/url"
	"testing"
	"time"

	"github.com/bluenviron/gortsplib/v5/pkg/base"
)

type testNetworkError struct{}

func (testNetworkError) Error() string   { return "temporary network error" }
func (testNetworkError) Timeout() bool   { return true }
func (testNetworkError) Temporary() bool { return true }

func TestRetryableSessionErrorClassification(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want bool
	}{
		{name: "discovery timeout", err: ErrDiscoveryTimeout, want: true},
		{name: "connection closed", err: ErrConnectionClosed, want: true},
		{name: "video interrupted", err: ErrStreamInterrupted, want: true},
		{name: "video startup", err: ErrVideoNotReady, want: true},
		{name: "RTSP EOF", err: io.EOF, want: true},
		{name: "network timeout", err: testNetworkError{}, want: true},
		{name: "authentication rejected", err: ErrAuthenticationRejected},
		{name: "protocol error", err: ErrProtocol},
		{name: "invalid media", err: ErrMediaFrame},
		{name: "critical publisher error", err: ErrVideoFatal},
		{name: "unknown error", err: errors.New("unknown")},
		{name: "canceled", err: context.Canceled},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := retryableSessionError(test.err); got != test.want {
				t.Fatalf("retryableSessionError(%v) = %v, want %v", test.err, got, test.want)
			}
		})
	}
}

func TestRetryableSessionErrorBlocksMalformedURL(t *testing.T) {
	err := &url.Error{Op: "parse", URL: "rtsp://[", Err: errors.New("missing bracket")}
	if retryableSessionError(err) {
		t.Fatal("malformed RTSP URL was classified as retryable")
	}
}

func TestRetryableSessionErrorRecognizesRTSPNetworkCause(t *testing.T) {
	err := errors.Join(ErrVideoStartup, &net.OpError{
		Op:  "dial",
		Net: "tcp",
		Err: testNetworkError{},
	})
	if !retryableSessionError(err) {
		t.Fatal("RTSP network failure was not classified as retryable")
	}
}

func TestRTSPStartupErrorClassifiesResponseStatus(t *testing.T) {
	cause := errors.New("RTSP response rejected request")
	tests := []struct {
		name       string
		status     base.StatusCode
		retryable  bool
		videoFatal bool
	}{
		{name: "internal server error", status: base.StatusInternalServerError, videoFatal: true},
		{name: "not implemented", status: base.StatusNotImplemented, videoFatal: true},
		{name: "bad gateway", status: base.StatusBadGateway, videoFatal: true},
		{name: "service unavailable", status: base.StatusServiceUnavailable, retryable: true},
		{name: "gateway timeout", status: base.StatusGatewayTimeout, videoFatal: true},
		{name: "RTSP version not supported", status: base.StatusRTSPVersionNotSupported, videoFatal: true},
		{name: "unknown server error", status: 599, videoFatal: true},
		{name: "bad request", status: 400, videoFatal: true},
		{name: "unauthorized", status: base.StatusUnauthorized, videoFatal: true},
		{name: "forbidden", status: base.StatusForbidden, videoFatal: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := rtspStartupError("ANNOUNCE", &base.Response{StatusCode: test.status}, cause)
			if got := retryableSessionError(err); got != test.retryable {
				t.Fatalf("retryableSessionError() = %v, want %v", got, test.retryable)
			}
			if got := errors.Is(err, ErrVideoFatal); got != test.videoFatal {
				t.Fatalf("errors.Is(ErrVideoFatal) = %v, want %v", got, test.videoFatal)
			}
			if !errors.Is(err, cause) {
				t.Fatal("RTSP startup error did not preserve its cause")
			}
		})
	}
}

func TestSessionDurationAcceptsUnboundedPositiveDurations(t *testing.T) {
	for _, duration := range []time.Duration{
		0,
		time.Second,
		25 * time.Hour,
		time.Duration(1<<63 - 1),
	} {
		if err := validateSessionDuration(duration); err != nil {
			t.Fatalf("validateSessionDuration(%s) = %v", duration, err)
		}
	}
	for _, duration := range []time.Duration{-time.Nanosecond, -time.Second} {
		if err := validateSessionDuration(duration); !errors.Is(err, ErrProtocol) {
			t.Fatalf("validateSessionDuration(%s) = %v, want ErrProtocol", duration, err)
		}
	}
}

func TestRunSessionRejectsInvalidConfigBeforeRTSPPreflight(t *testing.T) {
	err := RunSession(context.Background(), "not-an-ip", "192.0.2.20", "rtsp://127.0.0.1:1/test", 0)
	if err == nil || retryableSessionError(err) {
		t.Fatalf("RunSession() error = %v, want invalid configuration before network preflight", err)
	}
}

func TestNextBackoffDoublesAndCaps(t *testing.T) {
	delay := time.Second
	want := []time.Duration{2 * time.Second, 4 * time.Second, 8 * time.Second, 10 * time.Second, 10 * time.Second}
	for _, expected := range want {
		delay = nextBackoff(delay, 10*time.Second)
		if delay != expected {
			t.Fatalf("nextBackoff() = %s, want %s", delay, expected)
		}
	}
}

func TestSuperviseRetriesSequentiallyAndResetsAfterStableRun(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	policy := retryPolicy{
		initialDelay: time.Millisecond,
		maxDelay:     8 * time.Millisecond,
		stableAfter:  5 * time.Millisecond,
	}
	var attempts int
	var delays []time.Duration
	run := func(context.Context) error {
		attempts++
		switch attempts {
		case 1:
			return ErrDiscoveryTimeout
		case 2:
			time.Sleep(10 * time.Millisecond)
			return ErrStreamInterrupted
		default:
			cancel()
			return context.Canceled
		}
	}
	wait := func(ctx context.Context, delay time.Duration) bool {
		delays = append(delays, delay)
		return ctx.Err() == nil
	}

	if err := supervise(ctx, run, policy, wait); err != nil {
		t.Fatalf("supervise() error = %v", err)
	}
	if attempts != 3 {
		t.Fatalf("session attempts = %d, want 3", attempts)
	}
	if len(delays) != 2 || delays[0] != policy.initialDelay || delays[1] != policy.initialDelay {
		t.Fatalf("retry delays = %v, want initial delay after stable run", delays)
	}
}

func TestSuperviseReturnsAfterSuccessfulReconnect(t *testing.T) {
	attempts := 0
	waited := 0
	err := supervise(context.Background(), func(context.Context) error {
		attempts++
		if attempts == 1 {
			return io.EOF
		}
		return nil
	}, retryPolicy{initialDelay: time.Millisecond, maxDelay: time.Second}, func(context.Context, time.Duration) bool {
		waited++
		return true
	})
	if err != nil || attempts != 2 || waited != 1 {
		t.Fatalf("supervise() = %v, attempts = %d, waits = %d", err, attempts, waited)
	}
}

func TestSuperviseStopsOnFatalErrors(t *testing.T) {
	for _, failure := range []error{ErrAuthenticationRejected, ErrProtocol, ErrMediaFrame, ErrVideoFatal} {
		t.Run(failure.Error(), func(t *testing.T) {
			attempts := 0
			waited := false
			err := supervise(context.Background(), func(context.Context) error {
				attempts++
				return failure
			}, retryPolicy{initialDelay: time.Millisecond, maxDelay: time.Second}, func(context.Context, time.Duration) bool {
				waited = true
				return true
			})
			if !errors.Is(err, failure) || attempts != 1 || waited {
				t.Fatalf("supervise() = %v, attempts = %d, waited = %v", err, attempts, waited)
			}
		})
	}
}

func TestSuperviseStopsDuringBackoff(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	timer := time.AfterFunc(5*time.Millisecond, cancel)
	defer timer.Stop()
	attempts := 0
	started := time.Now()
	err := supervise(ctx, func(context.Context) error {
		attempts++
		return ErrDiscoveryTimeout
	}, retryPolicy{initialDelay: time.Hour, maxDelay: time.Hour}, waitForRetry)
	if err != nil || attempts != 1 {
		t.Fatalf("supervise() = %v, attempts = %d", err, attempts)
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("supervisor took %s to stop during backoff", elapsed)
	}
}
