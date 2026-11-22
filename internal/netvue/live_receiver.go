package netvue

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"time"
)

type liveReceiver struct {
	ctx             context.Context
	cancel          context.CancelFunc
	conn            net.Conn
	writer          *sessionWriter
	publisher       livePublisher
	startupTimeout  time.Duration
	silenceTimeout  time.Duration
	talk            *talkAudio
	live            *LiveTalkback
	talkDone        chan error
	talkStarted     bool
	talkComplete    bool
	channelReady    bool
	audioRequested  bool
	audioFallback   bool
	audioDirection  bool
	audioDisabled   bool
	talkDisabled    bool
	liveAttached    bool
	selectedAudio   *audioParameters
	lastVideo       time.Time
	lastPublished   time.Time
	lastTimestamp   uint32
	timestampAt     time.Time
	lastAccessUnit  []byte
	startupDeadline time.Time
	observe         func(SessionEvent)
	lastObserved    time.Time
}

func (receiver *liveReceiver) finishTalk(resultErr *error) {
	wasCanceled := receiver.ctx.Err() != nil
	receiver.cancel()
	if receiver.talkDone == nil {
		return
	}
	_ = receiver.conn.Close()
	err := <-receiver.talkDone
	if !wasCanceled && err != nil &&
		!errors.Is(err, context.Canceled) &&
		!errors.Is(err, context.DeadlineExceeded) {
		*resultErr = talkFailure("audio send failed", err)
	}
}

func (receiver *liveReceiver) pollTalk() error {
	if receiver.talkDone == nil {
		return nil
	}
	select {
	case err := <-receiver.talkDone:
		receiver.talkDone = nil
		if err != nil {
			return talkFailure("audio send failed", err)
		}
		receiver.talkComplete = true
	default:
	}
	return nil
}

func (receiver *liveReceiver) finishSessionIfEnded(now time.Time) (bool, error) {
	done, err := sessionEndResult(
		receiver.ctx, receiver.publisher, receiver.lastPublished, receiver.silenceTimeout, now,
	)
	if !done {
		return false, nil
	}
	if receiver.talk == nil ||
		!errors.Is(context.Cause(receiver.ctx), errSessionDurationElapsed) ||
		receiver.talkComplete {
		return true, err
	}
	if receiver.talkDone != nil {
		sendErr := <-receiver.talkDone
		receiver.talkDone = nil
		if sendErr == nil {
			receiver.talkComplete = true
		} else if !errors.Is(sendErr, context.Canceled) &&
			!errors.Is(sendErr, context.DeadlineExceeded) {
			return true, talkFailure("audio send failed", sendErr)
		}
	}
	if !receiver.talkComplete {
		return true, talkFailure(
			"session duration elapsed before audio completed",
			errSessionDurationElapsed,
		)
	}
	return true, err
}

func (receiver *liveReceiver) readPacket(now time.Time) (Packet, bool, error) {
	deadline := now.Add(receiver.silenceTimeout)
	if !receiver.lastVideo.IsZero() {
		deadline = receiver.lastVideo.Add(receiver.silenceTimeout)
	}
	if receiver.publisher.CurrentPublicationFrames() == 0 &&
		receiver.startupDeadline.Before(deadline) {
		deadline = receiver.startupDeadline
	}
	if ctxDeadline, ok := receiver.ctx.Deadline(); ok && ctxDeadline.Before(deadline) {
		deadline = ctxDeadline
	}
	if err := receiver.conn.SetReadDeadline(deadline); err != nil {
		if contextError(receiver.ctx) != nil {
			return Packet{}, true, nil
		}
		return Packet{}, false, atStage(
			"LIVE packet",
			fmt.Errorf("set Netvue read deadline: %w", err),
		)
	}

	packet, err := ReadPacket(receiver.conn)
	if err == nil {
		return packet, false, nil
	}
	if talkErr := receiver.pollTalk(); talkErr != nil {
		return Packet{}, false, talkErr
	}
	if contextError(receiver.ctx) != nil {
		return Packet{}, true, nil
	}
	var networkError net.Error
	if errors.As(err, &networkError) && networkError.Timeout() {
		if receiver.publisher.CurrentPublicationFrames() == 0 &&
			!time.Now().Before(receiver.startupDeadline) {
			return Packet{}, false, receiver.startupError(
				"no publication before startup deadline",
				"no video publication before startup deadline",
			)
		}
		return Packet{}, false, atStage("LIVE packet", withReason(
			"no video received before timeout",
			fmt.Errorf("%w: no video received before timeout", ErrStreamInterrupted),
		))
	}
	return Packet{}, false, atStage(
		"LIVE packet",
		fmt.Errorf("read Netvue LIVE packet: %w", err),
	)
}

func (receiver *liveReceiver) startupError(reason, detail string) error {
	return atStage("H.264", withReason(
		reason,
		fmt.Errorf("%w: %s", ErrVideoNotReady, detail),
	))
}

func (receiver *liveReceiver) handleCommand(body []byte) error {
	units, err := ParseUnits(body)
	if err != nil {
		return atStage("LIVE response", err)
	}
	for _, unit := range units {
		action, actionOK := unitInt(unit, "A")
		version, versionOK := unitInt(unit, "V")
		if receiver.talk != nil && actionOK && action == 5 {
			if result, ok := unitInt(unit, "C"); !ok || result != 0 {
				return talkFailure("audio direction rejected or invalid", ErrTalkback)
			}
		}
		if receiver.live != nil && actionOK && action == 5 {
			if result, ok := unitInt(unit, "C"); !ok || result != 0 {
				receiver.talkDisabled = true
				receiver.live.detachWithError(
					talkFailure("audio direction rejected or invalid", ErrTalkback),
				)
				receiver.liveAttached = false
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
			return atStage("LIVE response", withReason(
				reason,
				fmt.Errorf(
					"%w: %w (present=%t, value=%d)",
					ErrProtocol, ErrLiveResponseVersion, versionOK, version,
				),
			))
		}
		result, resultOK := unitInt(unit, "C")
		if !resultOK {
			return atStage("LIVE response", withReason(
				"missing or invalid result",
				fmt.Errorf("%w: LIVE response has no explicit result", ErrProtocol),
			))
		}
		if result != 0 {
			if receiver.talk != nil {
				return talkFailure(
					"LIVE audio selection rejected",
					fmt.Errorf("selection rejected, result_code=%d", result),
				)
			}
			if receiver.audioRequested && !receiver.audioFallback {
				if err := sendLiveRequestChannels(receiver.ctx, receiver.writer, false); err != nil {
					return atStage("LIVE request", err)
				}
				receiver.audioRequested = false
				receiver.audioFallback = true
				return nil
			}
			return atStage("LIVE response", withReason(
				"selection rejected",
				fmt.Errorf("%w: LIVE selection rejected, result_code=%d", ErrProtocol, result),
			))
		}

		oldAudio := receiver.selectedAudio
		receiver.selectedAudio, err = parseAudioParameters(unit)
		if err != nil {
			if receiver.talk != nil {
				return talkFailure("invalid selected audio", err)
			}
			if !errors.Is(err, ErrUnsupportedAudioParameters) {
				return atStage("LIVE response", err)
			}
			receiver.disableAudio(atStage("audio parameters", err))
			receiver.selectedAudio = nil
		}
		if receiver.live != nil && receiver.liveAttached &&
			(oldAudio == nil || receiver.selectedAudio == nil ||
				*oldAudio != *receiver.selectedAudio) {
			if err := receiver.live.invalidateSelection(receiver.ctx); err != nil {
				return err
			}
			receiver.liveAttached = false
		}
		if receiver.talk != nil &&
			(receiver.selectedAudio == nil ||
				receiver.selectedAudio.sampleRate != receiver.talk.rate ||
				receiver.selectedAudio.channels != receiver.talk.channels) {
			return talkFailure(
				"ADTS format does not match selected audio channel",
				ErrTalkback,
			)
		}
		if receiver.selectedAudio != nil && !receiver.audioDirection {
			if receiver.talk != nil {
				err = sendTalkDirection(receiver.ctx, receiver.writer)
			} else {
				err = sendAudioDirection(receiver.ctx, receiver.writer)
			}
			if err != nil {
				return atStage("audio direction", err)
			}
			receiver.audioDirection = true
		}
		receiver.channelReady = true
	}
	return nil
}

func (receiver *liveReceiver) handleMedia(body []byte) (bool, error) {
	if !receiver.channelReady {
		return false, atStage("LIVE response", withReason(
			"media before confirmation",
			fmt.Errorf("%w: media arrived before LIVE confirmation", ErrProtocol),
		))
	}
	if receiver.audioDisabled && isAudioPacket(body) {
		return false, nil
	}
	audio, isAudio, err := parseAudioFrame(body)
	if err != nil {
		if isAudioPacket(body) {
			receiver.disableAudio(err)
			return false, nil
		}
		return false, atStage("media frame", err)
	}
	if isAudio {
		return false, receiver.handleAudio(audio)
	}
	frame, isVideo, err := parseMediaFrame(body)
	if err != nil {
		return false, atStage("media frame", err)
	}
	if !isVideo {
		return false, nil
	}
	return receiver.handleVideo(frame)
}

func isAudioPacket(body []byte) bool {
	return len(body) > 0 && body[0]>>4 == audioFrameType
}

func (receiver *liveReceiver) handleAudio(audio audioFrame) error {
	if receiver.selectedAudio == nil {
		return nil
	}
	audioPublisher, ok := receiver.publisher.(liveAudioPublisher)
	if !ok {
		return atStage(
			"audio",
			fmt.Errorf("%w: publisher does not support audio", ErrAudioFrame),
		)
	}
	before := receiver.publisher.CurrentPublicationFrames()
	if err := audioPublisher.PushAudio(audio, receiver.selectedAudio); err != nil {
		if errors.Is(err, ErrAudioFrame) {
			receiver.disableAudio(err)
			return nil
		}
		return err
	}
	if before > 0 && receiver.publisher.CurrentPublicationFrames() == 0 {
		receiver.startupDeadline = time.Now().Add(receiver.startupTimeout)
		receiver.lastPublished = time.Time{}
		receiver.publisher.SetStartupDeadline(receiver.startupDeadline)
	}
	return nil
}

func (receiver *liveReceiver) handleVideo(frame videoFrame) (bool, error) {
	now := time.Now()
	if !receiver.timestampAt.IsZero() && frame.timestamp == receiver.lastTimestamp {
		if now.Sub(receiver.timestampAt) >= receiver.silenceTimeout {
			return false, atStage("H.264", withReason(
				"video timestamp stopped advancing",
				fmt.Errorf("%w: video timestamp stopped advancing", ErrStreamInterrupted),
			))
		}
		if bytes.Equal(frame.accessUnit, receiver.lastAccessUnit) {
			return false, nil
		}
	} else {
		receiver.timestampAt = now
	}
	receiver.lastTimestamp = frame.timestamp
	receiver.lastAccessUnit = frame.accessUnit
	beforeGeneration := receiver.publisher.Generation()
	before := receiver.publisher.CurrentPublicationFrames()
	startDeadline := receiver.startupDeadline
	if before > 0 {
		startDeadline = time.Now().Add(receiver.startupTimeout)
	}
	receiver.publisher.SetStartupDeadline(startDeadline)
	if err := receiver.publisher.Push(frame.accessUnit, frame.timestamp); err != nil {
		if contextError(receiver.ctx) != nil {
			return false, nil
		}
		if done, endErr := sessionEndResult(
			receiver.ctx,
			receiver.publisher,
			receiver.lastPublished,
			receiver.silenceTimeout,
			time.Now(),
		); done {
			return true, endErr
		}
		return false, atStage("H.264", err)
	}

	now = time.Now()
	receiver.lastVideo = now
	current := receiver.publisher.CurrentPublicationFrames()
	generationChanged := receiver.publisher.Generation() != beforeGeneration
	if generationChanged {
		receiver.startupDeadline = time.Time{}
		receiver.lastPublished = time.Time{}
	}
	if current > 0 {
		receiver.startupDeadline = time.Time{}
		if generationChanged || current > before {
			receiver.lastPublished = now
			if receiver.observe != nil && (receiver.lastObserved.IsZero() || now.Sub(receiver.lastObserved) >= time.Second) {
				receiver.observe(SessionEvent{FrameAt: now})
				receiver.lastObserved = now
			}
		}
		if !receiver.lastPublished.IsZero() &&
			now.Sub(receiver.lastPublished) > receiver.silenceTimeout {
			return false, atStage("H.264", withReason(
				"no published image before timeout",
				fmt.Errorf("%w: no published H.264 image before timeout", ErrStreamInterrupted),
			))
		}
		receiver.startTalkbackIfReady()
	} else {
		receiver.lastPublished = time.Time{}
		if receiver.startupDeadline.IsZero() || before > 0 {
			receiver.startupDeadline = now.Add(receiver.startupTimeout)
		}
		if !now.Before(receiver.startupDeadline) {
			return false, receiver.startupError(
				"no IDR after startup or parameter change",
				"no IDR after startup or parameter change",
			)
		}
	}
	return false, nil
}

func (receiver *liveReceiver) startTalkbackIfReady() {
	if receiver.live != nil && !receiver.liveAttached &&
		receiver.selectedAudio != nil && receiver.audioDirection && !receiver.talkDisabled {
		receiver.live.attachIfAbsent(receiver.ctx, receiver.writer, receiver.selectedAudio)
		receiver.liveAttached = true
	}
	if receiver.talk == nil || receiver.talkStarted || !receiver.audioDirection {
		return
	}
	receiver.talkStarted = true
	receiver.talkDone = make(chan error, 1)
	go func(done chan<- error) {
		err := receiver.talk.send(receiver.ctx, receiver.writer)
		if err == nil {
			slog.Info("Netvue talkback audio sent")
		}
		done <- err
		if err != nil {
			_ = receiver.conn.Close()
		}
	}(receiver.talkDone)
}

func (receiver *liveReceiver) disableAudio(err error) {
	if !receiver.audioDisabled {
		slog.Warn(
			"Netvue audio disabled; video will continue",
			"diagnostic", SessionDiagnostic(atStage("audio", err)),
		)
	}
	receiver.audioDisabled = true
}
