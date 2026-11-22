package netvue

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"math/rand/v2"
	"net"
	"net/url"
	"time"
)

type retryPolicy struct {
	initialDelay time.Duration
	maxDelay     time.Duration
	stableAfter  time.Duration
	jitter       func(time.Duration) time.Duration
}

var continuousRetryPolicy = retryPolicy{
	initialDelay: 2 * time.Second,
	maxDelay:     30 * time.Second,
	stableAfter:  5 * time.Minute,
	jitter:       jitterDelay,
}

type SessionEvent struct {
	FrameAt time.Time
	Error   string
}

// RunContinuous retries recoverable session failures until ctx is canceled.
func RunContinuous(ctx context.Context, source, target, rtspURL string) error {
	return RunContinuousWithTalkbackAndDiscovery(ctx, source, target, rtspURL, nil, nil)
}

// RunContinuousWithTalkback binds the controller only to each active session.
func RunContinuousWithTalkback(ctx context.Context, source, target, rtspURL string, live *LiveTalkback) error {
	return RunContinuousWithTalkbackAndDiscovery(ctx, source, target, rtspURL, live, nil)
}

// RunContinuousWithDiscovery reports each valid discovery without affecting the session.
func RunContinuousWithDiscovery(
	ctx context.Context,
	source, target, rtspURL string,
	onDiscovery func(DiscoveryResult),
) error {
	return RunContinuousWithTalkbackAndDiscovery(ctx, source, target, rtspURL, nil, onDiscovery)
}

// RunContinuousWithTalkbackAndDiscovery binds talkback and observes local discovery results.
func RunContinuousWithTalkbackAndDiscovery(
	ctx context.Context,
	source, target, rtspURL string,
	live *LiveTalkback,
	onDiscovery func(DiscoveryResult),
) error {
	return RunContinuousObserved(ctx, source, target, rtspURL, live, onDiscovery, nil)
}

func RunContinuousObserved(
	ctx context.Context,
	source, target, rtspURL string,
	live *LiveTalkback,
	onDiscovery func(DiscoveryResult),
	observe func(SessionEvent),
) error {
	cameraLabel := "invalid address"
	if ip, err := validateLANIPv4(target); err == nil {
		cameraLabel = ip.String()
	}
	var cached camera
	runSession := func(ctx context.Context) error {
		return runSessionWithCachedCamera(
			ctx, source, target, rtspURL, 0, nil, live, onDiscovery, &cached, observe,
		)
	}
	return superviseObserved(ctx, cameraLabel, runSession, continuousRetryPolicy, waitForRetry, observe)
}

func jitterDelay(delay time.Duration) time.Duration {
	spread := delay / 4
	if spread <= 0 {
		return delay
	}
	return delay - time.Duration(rand.Int64N(int64(spread)+1))
}

func supervise(
	ctx context.Context,
	target string,
	runSession func(context.Context) error,
	policy retryPolicy,
	wait func(context.Context, time.Duration) bool,
) error {
	return superviseObserved(ctx, target, runSession, policy, wait, nil)
}

func superviseObserved(
	ctx context.Context,
	target string,
	runSession func(context.Context) error,
	policy retryPolicy,
	wait func(context.Context, time.Duration) bool,
	observe func(SessionEvent),
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
		if observe != nil {
			observe(SessionEvent{Error: SessionDiagnostic(err)})
		}
		if time.Since(started) >= policy.stableAfter {
			delay = policy.initialDelay
		}
		retryDelay := delay
		if policy.jitter != nil {
			retryDelay = policy.jitter(delay)
		}
		slog.Warn("Camera session will retry", "camera", target, "error", SessionDiagnostic(err), "delay", retryDelay)
		if !wait(ctx, retryDelay) {
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
