package netvue

import (
	"context"
	"errors"
	"io"
	"net"
)

type diagnosticStage struct {
	stage string
	err   error
}

func (e *diagnosticStage) Error() string { return e.err.Error() }
func (e *diagnosticStage) Unwrap() error { return e.err }

type diagnosticReason struct {
	reason string
	err    error
}

func (e *diagnosticReason) Error() string { return e.err.Error() }
func (e *diagnosticReason) Unwrap() error { return e.err }

// Stages and reasons must be fixed strings, never data received from a peer.
func atStage(stage string, err error) error {
	var existing *diagnosticStage
	if errors.As(err, &existing) {
		return err
	}
	return &diagnosticStage{stage: stage, err: err}
}

func withReason(reason string, err error) error {
	return &diagnosticReason{reason: reason, err: err}
}

// SessionDiagnostic returns only locally defined text, not the underlying error.
func SessionDiagnostic(err error) string {
	var stage *diagnosticStage
	var reason *diagnosticReason
	var networkError net.Error
	errors.As(err, &stage)
	errors.As(err, &reason)

	var category string
	switch {
	case errors.Is(err, ErrAuthenticationRejected):
		category = "Camera authentication rejected"
	case errors.Is(err, ErrVideoFatal):
		category = "Video publication failed"
	case errors.Is(err, ErrUnsupportedAudioParameters):
		category = "Unsupported Netvue audio parameters"
	case errors.Is(err, ErrAudioFrame):
		category = "Unsupported or malformed Netvue audio"
	case stage != nil && stage.stage == "H.264" && errors.Is(err, ErrProtocol):
		category = "Invalid H.264 data"
	case errors.Is(err, ErrMediaFrame):
		category = "Invalid Netvue media frame"
	case errors.Is(err, ErrLiveResponseVersion):
		category = "Unsupported Netvue LIVE response version"
	case errors.Is(err, ErrProtocol):
		category = "Invalid Netvue protocol data"
	case errors.Is(err, ErrVideoNotReady), errors.Is(err, ErrStreamInterrupted):
		category = "Video stream not ready or interrupted"
	case errors.Is(err, ErrVideoStartup):
		category = "Video publication startup failed"
	case errors.Is(err, ErrTalkback):
		category = "Talkback failed"
	case errors.Is(err, ErrDiscoveryTimeout), errors.Is(err, ErrConnectionClosed),
		errors.Is(err, context.DeadlineExceeded), errors.Is(err, io.EOF),
		errors.Is(err, io.ErrUnexpectedEOF), errors.Is(err, io.ErrClosedPipe),
		errors.Is(err, net.ErrClosed), errors.As(err, &networkError):
		category = "Connection error"
	case stage != nil && stage.stage == "H.264":
		category = "H.264 processing failed"
	default:
		category = "Unexpected camera session error"
	}
	if stage != nil {
		category += " [" + stage.stage + "]"
	}
	if reason != nil {
		category += ": " + reason.reason
	}
	return category
}
