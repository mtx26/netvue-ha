package netvue

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/url"
	"time"
)

type retryPolicy struct {
	initialDelay time.Duration
	maxDelay     time.Duration
	stableAfter  time.Duration
}

var continuousRetryPolicy = retryPolicy{
	initialDelay: 2 * time.Second,
	maxDelay:     30 * time.Second,
	stableAfter:  5 * time.Minute,
}

// RunContinuous retries recoverable session failures until ctx is canceled.
func RunContinuous(ctx context.Context, source, target, rtspURL string) error {
	cameraLabel := "invalid address"
	if ip, err := validateLANIPv4(target); err == nil {
		cameraLabel = ip.String()
	}
	runSession := func(ctx context.Context) error {
		return RunSession(ctx, source, target, rtspURL, 0)
	}
	return supervise(ctx, cameraLabel, runSession, continuousRetryPolicy, waitForRetry)
}

func supervise(
	ctx context.Context,
	target string,
	runSession func(context.Context) error,
	policy retryPolicy,
	wait func(context.Context, time.Duration) bool,
) error {
	delay := policy.initialDelay
	for {
		if ctx.Err() != nil {
			return nil
		}
		started := time.Now()
		err := runSession(ctx)
		if ctx.Err() != nil {
			return nil
		}
		if err == nil {
			return nil
		}
		if !retryableSessionError(err) {
			return err
		}
		if time.Since(started) >= policy.stableAfter {
			delay = policy.initialDelay
		}
		slog.Warn("Camera session will retry", "camera", target, "error", SessionDiagnostic(err), "delay", delay)
		if !wait(ctx, delay) {
			return nil
		}
		delay = nextBackoff(delay, policy.maxDelay)
	}
}

func retryableSessionError(err error) bool {
	if errors.Is(err, ErrAuthenticationRejected) ||
		errors.Is(err, ErrProtocol) ||
		errors.Is(err, ErrMediaFrame) ||
		errors.Is(err, ErrVideoFatal) {
		return false
	}
	var urlError *url.Error
	if errors.As(err, &urlError) {
		return false
	}
	if errors.Is(err, ErrDiscoveryTimeout) ||
		errors.Is(err, ErrConnectionClosed) ||
		errors.Is(err, ErrStreamInterrupted) ||
		errors.Is(err, ErrVideoNotReady) ||
		errors.Is(err, ErrVideoStartup) ||
		errors.Is(err, context.DeadlineExceeded) ||
		errors.Is(err, io.EOF) ||
		errors.Is(err, io.ErrUnexpectedEOF) ||
		errors.Is(err, io.ErrClosedPipe) ||
		errors.Is(err, net.ErrClosed) {
		return true
	}
	var networkError net.Error
	return errors.As(err, &networkError)
}

func nextBackoff(current, maximum time.Duration) time.Duration {
	if current >= maximum/2 {
		return maximum
	}
	return current * 2
}

func waitForRetry(ctx context.Context, delay time.Duration) bool {
	timer := time.NewTimer(delay)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}
