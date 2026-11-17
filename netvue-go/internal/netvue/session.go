package netvue

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/url"
	"time"
)

const (
	connectTimeout      = 2 * time.Second
	videoSilenceTimeout = 3 * time.Second
	videoStartupTimeout = 5 * time.Second
	maxSessionDuration  = 24 * time.Hour
)

var (
	ErrStreamInterrupted = errors.New("Netvue video stream interrupted")
	// Only expiration of this session's own duration is a clean receive-loop end.
	errSessionDurationElapsed = errors.New("Netvue session duration elapsed")
)

// RunSession performs one explicit, time-bounded camera session.
func RunSession(ctx context.Context, source, target, rtspURL string, duration time.Duration) error {
	if duration <= 0 || duration > maxSessionDuration {
		return fmt.Errorf("%w: session duration must be between zero and 24 hours", ErrProtocol)
	}
	// The same deadline bounds local setup, discovery, authentication, LIVE and RTSP.
	sessionCtx, cancel := context.WithTimeoutCause(ctx, duration, errSessionDurationElapsed)
	defer cancel()
	if err := validateLocalRTSPURL(rtspURL); err != nil {
		return err
	}
	if err := checkRTSPListener(sessionCtx, rtspURL); err != nil {
		return err
	}
	sourceIP, err := validateLANIPv4(source)
	if err != nil {
		return fmt.Errorf("invalid source address: %w", err)
	}
	targetIP, err := validateLANIPv4(target)
	if err != nil {
		return fmt.Errorf("invalid camera address: %w", err)
	}
	if sourceIP.Equal(targetIP) {
		return fmt.Errorf("%w: source and camera addresses must differ", ErrProtocol)
	}

	device, err := discover(sessionCtx, sourceIP.String(), targetIP.String())
	if err != nil {
		return fmt.Errorf("Netvue discovery failed: %w", err)
	}
	conn, err := (&net.Dialer{Timeout: connectTimeout}).DialContext(
		sessionCtx, "tcp4", device.endpoint(),
	)
	if err != nil {
		return fmt.Errorf("connect to discovered Netvue endpoint: %w", err)
	}
	defer conn.Close()

	result, err := authenticate(sessionCtx, conn, device, loginTimeout)
	if err != nil {
		return err
	}
	if result != 0 {
		return fmt.Errorf("%w: result_code=%d", ErrAuthenticationRejected, result)
	}
	if err := contextError(sessionCtx); err != nil {
		return err
	}
	if err := sendLiveRequest(sessionCtx, conn); err != nil {
		return err
	}
	publisher, err := newVideoPublisher(sessionCtx, rtspURL)
	if err != nil {
		return err
	}
	defer publisher.Close()
	return receiveLive(sessionCtx, conn, publisher)
}

func checkRTSPListener(ctx context.Context, value string) error {
	parsed, err := url.Parse(value)
	if err != nil {
		return fmt.Errorf("parse RTSP output URL: %w", err)
	}
	ip := net.ParseIP(parsed.Hostname())
	if ip == nil {
		return errors.New("RTSP loopback URL has an invalid IP address")
	}
	network := "tcp4"
	if ip.To4() == nil {
		network = "tcp6"
	}
	conn, err := (&net.Dialer{Timeout: connectTimeout}).DialContext(
		ctx, network, net.JoinHostPort(parsed.Hostname(), parsed.Port()),
	)
	if err != nil {
		return fmt.Errorf("RTSP loopback listener is unavailable: %w", err)
	}
	if err := conn.Close(); err != nil {
		return fmt.Errorf("close RTSP preflight connection: %w", err)
	}
	return nil
}

func sendLiveRequest(ctx context.Context, conn net.Conn) error {
	if err := contextError(ctx); err != nil {
		return err
	}
	now := time.Now().Unix()
	packet, err := EncodeCommand([]map[string]any{{
		"Q": 3,
		"V": 2,
		"P": []any{1, [][]int{{1, 0}}, []int64{now, now + 31_536_000}},
	}})
	if err != nil {
		return err
	}
	deadline := time.Now().Add(connectTimeout)
	if ctxDeadline, ok := ctx.Deadline(); ok && ctxDeadline.Before(deadline) {
		deadline = ctxDeadline
	}
	if err := conn.SetWriteDeadline(deadline); err != nil {
		return fmt.Errorf("set LIVE request deadline: %w", err)
	}
	stopCancel := context.AfterFunc(ctx, func() {
		_ = conn.Close()
	})
	defer stopCancel()
	if err := contextError(ctx); err != nil {
		return err
	}
	if err := writeFull(conn, packet); err != nil {
		if ctxErr := contextError(ctx); ctxErr != nil {
			return ctxErr
		}
		return fmt.Errorf("send LIVE request: %w", err)
	}
	if err := contextError(ctx); err != nil {
		return err
	}
	return nil
}

type livePublisher interface {
	Push([]byte, uint32) error
	SetStartupDeadline(time.Time)
	PublishedFrames() uint64
	CurrentPublicationFrames() uint64
	Generation() uint64
}

func receiveLive(ctx context.Context, conn net.Conn, publisher livePublisher) error {
	return receiveLiveWithTimeouts(ctx, conn, publisher, videoStartupTimeout, videoSilenceTimeout)
}

func receiveLiveWithTimeouts(
	ctx context.Context,
	conn net.Conn,
	publisher livePublisher,
	startupTimeout time.Duration,
	silenceTimeout time.Duration,
) error {
	if startupTimeout <= 0 || silenceTimeout <= 0 {
		return fmt.Errorf("%w: video timeouts must be positive", ErrProtocol)
	}
	channelReady := false
	var lastVideo time.Time
	var lastPublished time.Time
	// Control packets and non-video media cannot extend the first-video deadline.
	startupDeadline := time.Now().Add(startupTimeout)
	// Closing the connection cannot be undone by a later SetReadDeadline call.
	stopCancel := context.AfterFunc(ctx, func() {
		_ = conn.Close()
	})
	defer stopCancel()

	for {
		now := time.Now()
		if done, err := sessionEndResult(ctx, publisher, lastPublished, silenceTimeout, now); done {
			return err
		}
		if publisher.CurrentPublicationFrames() == 0 && !now.Before(startupDeadline) {
			return fmt.Errorf("%w: no video publication before startup deadline", ErrVideoStartup)
		}
		deadline := now.Add(silenceTimeout)
		if !lastVideo.IsZero() {
			deadline = lastVideo.Add(silenceTimeout)
		}
		if publisher.CurrentPublicationFrames() == 0 && startupDeadline.Before(deadline) {
			deadline = startupDeadline
		}
		if ctxDeadline, ok := ctx.Deadline(); ok && ctxDeadline.Before(deadline) {
			deadline = ctxDeadline
		}
		if err := conn.SetReadDeadline(deadline); err != nil {
			if contextError(ctx) != nil {
				continue
			}
			return fmt.Errorf("set Netvue read deadline: %w", err)
		}
		packet, err := ReadPacket(conn)
		if err != nil {
			if contextError(ctx) != nil {
				continue
			}
			var networkError net.Error
			if errors.As(err, &networkError) && networkError.Timeout() {
				if publisher.CurrentPublicationFrames() == 0 && !time.Now().Before(startupDeadline) {
					return fmt.Errorf("%w: no video publication before startup deadline", ErrVideoStartup)
				}
				return fmt.Errorf("%w: no video received before timeout", ErrStreamInterrupted)
			}
			return fmt.Errorf("read Netvue LIVE packet: %w", err)
		}
		switch packet.Kind {
		case packetCommand:
			units, err := ParseUnits(packet.Body)
			if err != nil {
				return err
			}
			for _, unit := range units {
				action, actionOK := unitInt(unit, "A")
				version, versionOK := unitInt(unit, "V")
				if !actionOK || action != 3 {
					continue
				}
				if !versionOK || version != 2 {
					return fmt.Errorf("%w: unsupported LIVE response version", ErrProtocol)
				}
				result, resultOK := unitInt(unit, "C")
				if !resultOK {
					return fmt.Errorf("%w: LIVE response has no explicit result", ErrProtocol)
				}
				if result != 0 {
					return fmt.Errorf("%w: LIVE selection rejected, result_code=%d", ErrProtocol, result)
				}
				channelReady = true
			}
		case packetMedia:
			if !channelReady {
				return fmt.Errorf("%w: media arrived before LIVE confirmation", ErrProtocol)
			}
			frame, isVideo, err := parseMediaFrame(packet.Body)
			if err != nil {
				return err
			}
			if !isVideo {
				continue
			}
			beforeGeneration := publisher.Generation()
			before := publisher.CurrentPublicationFrames()
			startDeadline := startupDeadline
			if before > 0 {
				startDeadline = time.Now().Add(startupTimeout)
			}
			publisher.SetStartupDeadline(startDeadline)
			if err := publisher.Push(frame.accessUnit, frame.timestamp); err != nil {
				if done, endErr := sessionEndResult(
					ctx, publisher, lastPublished, silenceTimeout, time.Now(),
				); done {
					return endErr
				}
				if contextError(ctx) != nil {
					continue
				}
				return err
			}
			now = time.Now()
			lastVideo = now
			current := publisher.CurrentPublicationFrames()
			generationChanged := publisher.Generation() != beforeGeneration
			if generationChanged {
				startupDeadline = time.Time{}
				lastPublished = time.Time{}
			}
			if current > 0 {
				startupDeadline = time.Time{}
				if generationChanged || current > before {
					lastPublished = now
				}
				if !lastPublished.IsZero() && now.Sub(lastPublished) > silenceTimeout {
					return fmt.Errorf("%w: no published H.264 image before timeout", ErrStreamInterrupted)
				}
			} else {
				lastPublished = time.Time{}
				if startupDeadline.IsZero() || before > 0 {
					startupDeadline = now.Add(startupTimeout)
				}
				if !now.Before(startupDeadline) {
					return fmt.Errorf("%w: no IDR after startup or parameter change", ErrVideoStartup)
				}
			}

		default:
			return fmt.Errorf("%w: unexpected packet type during LIVE", ErrProtocol)
		}
	}
}

func sessionEndResult(
	ctx context.Context,
	publisher livePublisher,
	lastPublished time.Time,
	silenceTimeout time.Duration,
	now time.Time,
) (bool, error) {
	err, cause := contextStatus(ctx)
	if err == nil {
		return false, nil
	}
	if !errors.Is(cause, errSessionDurationElapsed) {
		return true, err
	}
	if publisher.CurrentPublicationFrames() > 0 &&
		!lastPublished.IsZero() && now.Sub(lastPublished) <= silenceTimeout {
		return true, nil
	}
	return true, fmt.Errorf("%w: no active RTSP publication at session end", ErrVideoStartup)
}
