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
)

var (
	ErrStreamInterrupted      = errors.New("Netvue video stream interrupted")
	ErrLiveResponseVersion    = errors.New("unsupported LIVE response version")
	errSessionDurationElapsed = errors.New("Netvue session duration elapsed")
)

func validateSessionDuration(duration time.Duration) error {
	if duration < 0 {
		return fmt.Errorf("%w: session duration must be non-negative", ErrProtocol)
	}
	return nil
}

// RunSession runs one session; zero disables only the duration limit.
// Cancellation and stream failures can still end the session.
func RunSession(ctx context.Context, source, target, rtspURL string, duration time.Duration) error {
	return runSession(ctx, source, target, rtspURL, duration, nil)
}

func runSession(ctx context.Context, source, target, rtspURL string, duration time.Duration, talk *talkAudio) error {
	return runSessionWithLiveTalk(ctx, source, target, rtspURL, duration, talk, nil)
}

func runSessionWithLiveTalk(ctx context.Context, source, target, rtspURL string, duration time.Duration, talk *talkAudio, live *LiveTalkback) error {
	if err := validateSessionDuration(duration); err != nil {
		return err
	}
	var sessionCtx context.Context
	var cancel context.CancelFunc
	if duration == 0 {
		sessionCtx, cancel = context.WithCancel(ctx)
	} else {
		sessionCtx, cancel = context.WithTimeoutCause(ctx, duration, errSessionDurationElapsed)
	}
	defer cancel()
	if err := validateLocalRTSPURL(rtspURL); err != nil {
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
	if err := checkRTSPListener(sessionCtx, rtspURL); err != nil {
		return atStage("RTSP preflight", err)
	}

	device, err := discover(sessionCtx, sourceIP.String(), targetIP.String())
	if err != nil {
		return atStage("discovery", fmt.Errorf("Netvue discovery failed: %w", err))
	}
	if talk != nil && !device.audio {
		return talkFailure("camera did not advertise an audio channel", ErrUnsupportedAudioParameters)
	}
	conn, err := (&net.Dialer{Timeout: connectTimeout}).DialContext(
		sessionCtx, "tcp4", device.endpoint(),
	)
	if err != nil {
		return atStage("camera connection", fmt.Errorf("connect to discovered Netvue endpoint: %w", err))
	}
	defer conn.Close()

	result, err := authenticate(sessionCtx, conn, device, loginTimeout)
	if err != nil {
		return atStage("authentication", err)
	}
	if result != 0 {
		return atStage("authentication", fmt.Errorf("%w: result_code=%d", ErrAuthenticationRejected, result))
	}
	if err := contextError(sessionCtx); err != nil {
		return err
	}
	if err := sendLiveRequestChannels(sessionCtx, conn, device.audio); err != nil {
		return atStage("LIVE request", err)
	}
	publisher, err := newVideoPublisher(sessionCtx, rtspURL)
	if err != nil {
		return err
	}
	defer publisher.Close()
	return receiveLiveWithTalkController(sessionCtx, conn, publisher, videoStartupTimeout, videoSilenceTimeout, device.audio, talk, live)
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
	return sendLiveRequestChannels(ctx, conn, false)
}

func sendLiveRequestChannels(ctx context.Context, conn net.Conn, includeAudio bool) error {
	if err := contextError(ctx); err != nil {
		return err
	}
	now := time.Now().Unix()
	channels := [][]int{{1, 0}}
	if includeAudio {
		channels = append(channels, []int{2, 0})
	}
	packet, err := EncodeCommand([]map[string]any{{
		"Q": 3,
		"V": 2,
		"P": []any{1, channels, []int64{now, now + 31_536_000}},
	}})
	if err != nil {
		return err
	}
	return sendSessionCommand(ctx, conn, packet, "LIVE request")
}

func sendAudioDirection(ctx context.Context, conn net.Conn) error {
	packet, err := EncodeCommand([]map[string]any{{
		"Q": 5,
		"V": 1,
		"P": []int{1},
	}})
	if err != nil {
		return err
	}
	return sendSessionCommand(ctx, conn, packet, "audio direction request")
}

func sendSessionCommand(ctx context.Context, conn net.Conn, packet []byte, operation string) error {
	if writer, ok := conn.(*sessionWriter); ok {
		writer.mu.Lock()
		defer writer.mu.Unlock()
	}
	if err := contextError(ctx); err != nil {
		return err
	}
	deadline := time.Now().Add(connectTimeout)
	if ctxDeadline, ok := ctx.Deadline(); ok && ctxDeadline.Before(deadline) {
		deadline = ctxDeadline
	}
	if err := conn.SetWriteDeadline(deadline); err != nil {
		if ctxErr := contextError(ctx); ctxErr != nil {
			return ctxErr
		}
		return fmt.Errorf("set %s deadline: %w", operation, err)
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
		return fmt.Errorf("send %s: %w", operation, err)
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

type liveAudioPublisher interface {
	PushAudio(audioFrame, *audioParameters) error
}

func receiveLive(ctx context.Context, conn net.Conn, publisher livePublisher) error {
	return receiveLiveWithAudio(ctx, conn, publisher, false)
}

func receiveLiveWithAudio(ctx context.Context, conn net.Conn, publisher livePublisher, requestAudio bool) error {
	return receiveLiveWithTimeoutsAndAudio(
		ctx, conn, publisher, videoStartupTimeout, videoSilenceTimeout, requestAudio,
	)
}

func receiveLiveWithTimeouts(
	ctx context.Context,
	conn net.Conn,
	publisher livePublisher,
	startupTimeout time.Duration,
	silenceTimeout time.Duration,
) error {
	return receiveLiveWithTimeoutsAndAudio(ctx, conn, publisher, startupTimeout, silenceTimeout, false)
}

func receiveLiveWithTimeoutsAndAudio(
	ctx context.Context,
	conn net.Conn,
	publisher livePublisher,
	startupTimeout time.Duration,
	silenceTimeout time.Duration,
	requestAudio bool,
) error {
	return receiveLiveTalk(ctx, conn, publisher, startupTimeout, silenceTimeout, requestAudio, nil)
}

func receiveLiveTalk(
	ctx context.Context,
	conn net.Conn,
	publisher livePublisher,
	startupTimeout, silenceTimeout time.Duration,
	requestAudio bool,
	talk *talkAudio,
) (resultErr error) {
	return receiveLiveWithTalkController(ctx, conn, publisher, startupTimeout, silenceTimeout, requestAudio, talk, nil)
}

func receiveLiveWithTalkController(
	ctx context.Context,
	conn net.Conn,
	publisher livePublisher,
	startupTimeout, silenceTimeout time.Duration,
	requestAudio bool,
	talk *talkAudio,
	live *LiveTalkback,
) (resultErr error) {
	if startupTimeout <= 0 || silenceTimeout <= 0 {
		return fmt.Errorf("%w: video timeouts must be positive", ErrProtocol)
	}
	ctx, cancel := context.WithCancel(ctx)
	defer func() {
		cancel()
		if live != nil {
			live.detach()
		}
	}()
	writer := &sessionWriter{Conn: conn}
	receiver := liveReceiver{
		ctx:            ctx,
		cancel:         cancel,
		conn:           conn,
		writer:         writer,
		publisher:      publisher,
		startupTimeout: startupTimeout,
		silenceTimeout: silenceTimeout,
		talk:           talk,
		live:           live,
		audioRequested: requestAudio,
	}
	defer receiver.finishTalk(&resultErr)
	// Control packets and non-video media cannot extend the first-video deadline.
	receiver.startupDeadline = time.Now().Add(startupTimeout)
	// Closing the connection cannot be undone by a later SetReadDeadline call.
	stopCancel := context.AfterFunc(ctx, func() {
		_ = conn.Close()
	})
	defer stopCancel()
	for {
		now := time.Now()
		if err := receiver.pollTalk(); err != nil {
			return err
		}
		if done, err := receiver.finishSessionIfEnded(now); done {
			return err
		}
		if publisher.CurrentPublicationFrames() == 0 &&
			!now.Before(receiver.startupDeadline) {
			return receiver.startupError(
				"no publication before startup deadline",
				"no video publication before startup deadline",
			)
		}
		packet, retry, err := receiver.readPacket(now)
		if err != nil {
			return err
		}
		if retry {
			continue
		}
		switch packet.Kind {
		case packetCommand:
			if err := receiver.handleCommand(packet.Body); err != nil {
				return err
			}
		case packetMedia:
			done, err := receiver.handleMedia(packet.Body)
			if done || err != nil {
				return err
			}

		default:
			return atStage("LIVE packet", withReason("unexpected packet type", fmt.Errorf("%w: unexpected packet type during LIVE", ErrProtocol)))
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
