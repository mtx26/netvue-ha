package netvue

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
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

// RunSession runs one session; a zero duration runs until ctx ends.
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
	var talkDone chan error
	talkStarted := false
	talkComplete := false
	defer func() {
		wasCanceled := ctx.Err() != nil
		cancel()
		if talkDone != nil {
			_ = conn.Close()
			err := <-talkDone
			if !wasCanceled && err != nil && !errors.Is(err, context.Canceled) && !errors.Is(err, context.DeadlineExceeded) {
				resultErr = talkFailure("audio send failed", err)
			}
		}
	}()
	pollTalk := func() error {
		if talkDone == nil {
			return nil
		}
		select {
		case err := <-talkDone:
			talkDone = nil
			if err != nil {
				return talkFailure("audio send failed", err)
			}
			talkComplete = true
		default:
		}
		return nil
	}
	channelReady := false
	audioSelectionRequested := requestAudio
	audioFallbackUsed := false
	audioDirectionEnabled := false
	audioDisabled := false
	talkDisabled := false
	liveAttached := false
	var selectedAudio *audioParameters
	var lastVideo time.Time
	var lastPublished time.Time
	// Control packets and non-video media cannot extend the first-video deadline.
	startupDeadline := time.Now().Add(startupTimeout)
	// Closing the connection cannot be undone by a later SetReadDeadline call.
	stopCancel := context.AfterFunc(ctx, func() {
		_ = conn.Close()
	})
	defer stopCancel()
	disableAudio := func(err error) {
		if !audioDisabled {
			slog.Warn(
				"Netvue audio disabled; video will continue",
				"diagnostic", SessionDiagnostic(atStage("audio", err)),
			)
		}
		audioDisabled = true
	}

	for {
		now := time.Now()
		if err := pollTalk(); err != nil {
			return err
		}
		if done, err := sessionEndResult(ctx, publisher, lastPublished, silenceTimeout, now); done {
			if talk != nil && errors.Is(context.Cause(ctx), errSessionDurationElapsed) && !talkComplete {
				if talkDone != nil {
					sendErr := <-talkDone
					talkDone = nil
					if sendErr == nil {
						talkComplete = true
					} else if !errors.Is(sendErr, context.Canceled) && !errors.Is(sendErr, context.DeadlineExceeded) {
						return talkFailure("audio send failed", sendErr)
					}
				}
				if !talkComplete {
					return talkFailure("session duration elapsed before audio completed", errSessionDurationElapsed)
				}
			}
			return err
		}
		if publisher.CurrentPublicationFrames() == 0 && !now.Before(startupDeadline) {
			return atStage("H.264", withReason("no publication before startup deadline", fmt.Errorf("%w: no video publication before startup deadline", ErrVideoNotReady)))
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
			return atStage("LIVE packet", fmt.Errorf("set Netvue read deadline: %w", err))
		}
		packet, err := ReadPacket(conn)
		if err != nil {
			if talkErr := pollTalk(); talkErr != nil {
				return talkErr
			}
			if contextError(ctx) != nil {
				continue
			}
			var networkError net.Error
			if errors.As(err, &networkError) && networkError.Timeout() {
				if publisher.CurrentPublicationFrames() == 0 && !time.Now().Before(startupDeadline) {
					return atStage("H.264", withReason("no publication before startup deadline", fmt.Errorf("%w: no video publication before startup deadline", ErrVideoNotReady)))
				}
				return atStage("LIVE packet", withReason("no video received before timeout", fmt.Errorf("%w: no video received before timeout", ErrStreamInterrupted)))
			}
			return atStage("LIVE packet", fmt.Errorf("read Netvue LIVE packet: %w", err))
		}
		switch packet.Kind {
		case packetCommand:
			units, err := ParseUnits(packet.Body)
			if err != nil {
				return atStage("LIVE response", err)
			}
			retryVideoOnly := false
			for _, unit := range units {
				action, actionOK := unitInt(unit, "A")
				version, versionOK := unitInt(unit, "V")
				if talk != nil && actionOK && action == 5 {
					if result, ok := unitInt(unit, "C"); !ok || result != 0 {
						return talkFailure("audio direction rejected or invalid", ErrTalkback)
					}
				}
				if live != nil && actionOK && action == 5 {
					if result, ok := unitInt(unit, "C"); !ok || result != 0 {
						talkDisabled = true
						live.detachWithError(talkFailure("audio direction rejected or invalid", ErrTalkback))
						liveAttached = false
						disableAudio(talkFailure("audio direction rejected or invalid", ErrTalkback))
					}
				}
				if !actionOK || action != 3 {
					continue
				}
				if !versionOK || (version != 1 && version != 2) {
					reason := "unsupported version"
					if !versionOK {
						reason = "missing or invalid version"
					}
					return atStage("LIVE response", withReason(reason, fmt.Errorf("%w: %w (present=%t, value=%d)", ErrProtocol, ErrLiveResponseVersion, versionOK, version)))
				}
				result, resultOK := unitInt(unit, "C")
				if !resultOK {
					return atStage("LIVE response", withReason("missing or invalid result", fmt.Errorf("%w: LIVE response has no explicit result", ErrProtocol)))
				}
				if result != 0 {
					if talk != nil {
						return talkFailure("LIVE audio selection rejected", fmt.Errorf("selection rejected, result_code=%d", result))
					}
					if audioSelectionRequested && !audioFallbackUsed {
						if err := sendLiveRequestChannels(ctx, writer, false); err != nil {
							return atStage("LIVE request", err)
						}
						audioSelectionRequested = false
						audioFallbackUsed = true
						retryVideoOnly = true
						break
					}
					return atStage("LIVE response", withReason("selection rejected", fmt.Errorf("%w: LIVE selection rejected, result_code=%d", ErrProtocol, result)))
				}
				oldAudio := selectedAudio
				selectedAudio, err = parseAudioParameters(unit)
				if err != nil {
					if talk != nil {
						return talkFailure("invalid selected audio", err)
					}
					if !errors.Is(err, ErrUnsupportedAudioParameters) {
						return atStage("LIVE response", err)
					}
					disableAudio(atStage("audio parameters", err))
					selectedAudio = nil
				}
				if live != nil && liveAttached && (oldAudio == nil || selectedAudio == nil || *oldAudio != *selectedAudio) {
					if err := live.invalidateSelection(ctx); err != nil {
						return err
					}
					liveAttached = false
				}
				if talk != nil {
					if selectedAudio == nil || selectedAudio.sampleRate != talk.rate || selectedAudio.channels != talk.channels {
						return talkFailure("ADTS format does not match selected audio channel", ErrTalkback)
					}
				}
				if selectedAudio != nil && !audioDirectionEnabled {
					if talk != nil {
						if err := sendTalkDirection(ctx, writer); err != nil {
							return atStage("audio direction", err)
						}
					} else if err := sendAudioDirection(ctx, writer); err != nil {
						return atStage("audio direction", err)
					}
					audioDirectionEnabled = true
				}
				channelReady = true
			}
			if retryVideoOnly {
				continue
			}
		case packetMedia:
			if !channelReady {
				return atStage("LIVE response", withReason("media before confirmation", fmt.Errorf("%w: media arrived before LIVE confirmation", ErrProtocol)))
			}
			if audioDisabled && len(packet.Body) > 0 && packet.Body[0]>>4 == audioFrameType {
				continue
			}
			audio, isAudio, err := parseAudioFrame(packet.Body)
			if err != nil {
				if len(packet.Body) > 0 && packet.Body[0]>>4 == audioFrameType {
					disableAudio(err)
					continue
				}
				return atStage("media frame", err)
			}
			if isAudio {
				if selectedAudio == nil {
					continue
				}
				audioPublisher, ok := publisher.(liveAudioPublisher)
				if !ok {
					return atStage("audio", fmt.Errorf("%w: publisher does not support audio", ErrAudioFrame))
				}
				before := publisher.CurrentPublicationFrames()
				if err := audioPublisher.PushAudio(audio, selectedAudio); err != nil {
					if errors.Is(err, ErrAudioFrame) {
						disableAudio(err)
						continue
					}
					return err
				}
				if before > 0 && publisher.CurrentPublicationFrames() == 0 {
					startupDeadline = time.Now().Add(startupTimeout)
					lastPublished = time.Time{}
					publisher.SetStartupDeadline(startupDeadline)
				}
				continue
			}
			frame, isVideo, err := parseMediaFrame(packet.Body)
			if err != nil {
				return atStage("media frame", err)
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
				if contextError(ctx) != nil {
					continue
				}
				if done, endErr := sessionEndResult(
					ctx, publisher, lastPublished, silenceTimeout, time.Now(),
				); done {
					return endErr
				}
				return atStage("H.264", err)
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
					return atStage("H.264", withReason("no published image before timeout", fmt.Errorf("%w: no published H.264 image before timeout", ErrStreamInterrupted)))
				}
				if live != nil && !liveAttached && selectedAudio != nil && audioDirectionEnabled && !talkDisabled {
					live.attachIfAbsent(ctx, writer, selectedAudio)
					liveAttached = true
				}
				if talk != nil && !talkStarted && audioDirectionEnabled {
					talkStarted = true
					talkDone = make(chan error, 1)
					go func(done chan<- error) {
						err := talk.send(ctx, writer)
						if err == nil {
							slog.Info("Netvue talkback audio sent")
						}
						done <- err
						if err != nil {
							_ = conn.Close()
						}
					}(talkDone)
				}
			} else {
				lastPublished = time.Time{}
				if startupDeadline.IsZero() || before > 0 {
					startupDeadline = now.Add(startupTimeout)
				}
				if !now.Before(startupDeadline) {
					return atStage("H.264", withReason("no IDR after startup or parameter change", fmt.Errorf("%w: no IDR after startup or parameter change", ErrVideoNotReady)))
				}
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
