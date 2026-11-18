package netvue

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"time"
)

var (
	ErrTalkUnavailable = errors.New("live talkback session unavailable")
	ErrTalkBusy        = errors.New("live talkback already in use")
	ErrTalkClosed      = errors.New("live talkback stream closed")
)

// LiveTalkback belongs to one camera and survives supervisor reconnections.
// Construct it with NewLiveTalkback.
type LiveTalkback struct {
	mu      chan struct{}
	session *liveTalkSession
}

type liveTalkSession struct {
	ctx    context.Context
	writer *sessionWriter
	params audioParameters
	active *TalkStream
}

// TalkStream is bound to the current session, not a future reconnection.
type TalkStream struct {
	owner   *LiveTalkback
	session *liveTalkSession
	next    time.Time
	closed  bool
	done    chan struct{}
	err     error
}

// Done closes when the stream is closed or its camera session becomes invalid.
func (stream *TalkStream) Done() <-chan struct{} { return stream.done }

// Err reports the reason after Done closes.
func (stream *TalkStream) Err() error {
	<-stream.done
	<-stream.owner.mu
	defer stream.owner.unlock()
	return stream.err
}

func (stream *TalkStream) end(err error) {
	if !stream.closed {
		stream.closed = true
		stream.err = err
		close(stream.done)
	}
}

// AudioFormat returns the selected LIVE audio sample rate and channel count.
// The stream remains valid only until the current camera session ends.
func (stream *TalkStream) AudioFormat() (int, int) {
	return stream.session.params.sampleRate, stream.session.params.channels
}

// NewLiveTalkback creates a controller for a single camera.
func NewLiveTalkback() *LiveTalkback {
	c := &LiveTalkback{mu: make(chan struct{}, 1)}
	c.mu <- struct{}{}
	return c
}

func (c *LiveTalkback) lock(ctx context.Context) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-c.mu:
		if err := ctx.Err(); err != nil {
			c.unlock()
			return err
		}
		return nil
	}
}

func (c *LiveTalkback) unlock() { c.mu <- struct{}{} }

func (c *LiveTalkback) attachIfAbsent(ctx context.Context, writer *sessionWriter, params *audioParameters) {
	<-c.mu
	if c.session == nil {
		c.session = &liveTalkSession{ctx: ctx, writer: writer, params: *params}
	}
	c.unlock()
}

func (c *LiveTalkback) detach() {
	c.detachWithError(ErrTalkClosed)
}

func (c *LiveTalkback) detachWithError(err error) {
	<-c.mu
	if c.session != nil && c.session.active != nil {
		c.session.active.end(err)
	}
	c.session = nil
	c.unlock()
}

// invalidateSelection stops a stream when the selected format changes.
func (c *LiveTalkback) invalidateSelection(ctx context.Context) error {
	if err := c.lock(ctx); err != nil {
		return err
	}
	defer c.unlock()
	s := c.session
	if s == nil {
		return nil
	}
	c.session = nil
	if s.active == nil {
		return nil
	}
	s.active.end(ErrTalkClosed)
	s.active = nil
	return liveTalkCommand(ctx, s, sendLiveAudioDirection)
}

// Open requests bidirectional audio. A successful write is not a camera ACK
// or proof that the speaker plays audio. The caller must Close the stream.
func (c *LiveTalkback) Open(ctx context.Context) (*TalkStream, error) {
	if ctx == nil {
		return nil, errors.New("talkback context is required")
	}
	if err := c.lock(ctx); err != nil {
		return nil, err
	}
	defer c.unlock()
	s := c.session
	if s == nil || s.ctx.Err() != nil {
		return nil, ErrTalkUnavailable
	}
	if s.active != nil {
		return nil, ErrTalkBusy
	}
	if err := liveTalkCommand(ctx, s, sendLiveTalkDirection); err != nil {
		return nil, err
	}
	stream := &TalkStream{owner: c, session: s, done: make(chan struct{})}
	s.active = stream
	return stream, nil
}

// WriteADTS sends exactly one AAC-LC ADTS frame; it applies backpressure and
// paces frames rather than buffering or replaying audio after a slow write.
func (stream *TalkStream) WriteADTS(ctx context.Context, frame []byte) error {
	if ctx == nil {
		return errors.New("talkback context is required")
	}
	if len(frame) > 8191 {
		return talkFailure("invalid ADTS frame", ErrAudioFrame)
	}
	audio, err := readTalkAudio(bytes.NewReader(frame))
	if err != nil || len(audio.frames) != 1 {
		return talkFailure("expected one AAC-LC ADTS frame", ErrAudioFrame)
	}
	c := stream.owner
	if err := c.lock(ctx); err != nil {
		return err
	}
	defer c.unlock()
	s := stream.session
	if stream.closed || c.session != s || s.ctx.Err() != nil {
		return ErrTalkClosed
	}
	if audio.rate != s.params.sampleRate || audio.channels != s.params.channels {
		return talkFailure("ADTS format does not match selected audio channel", ErrUnsupportedAudioParameters)
	}
	opCtx, cancel := context.WithCancel(ctx)
	stop := context.AfterFunc(s.ctx, cancel)
	defer func() { stop(); cancel() }()
	if !stream.next.IsZero() {
		if err := waitTalk(opCtx, stream.next); err != nil {
			return err
		}
	}
	now := time.Now()
	body := make([]byte, headerSize+5+len(frame))
	binary.LittleEndian.PutUint32(body[:4], uint32(5+len(frame)))
	binary.LittleEndian.PutUint16(body[14:16], packetMedia)
	body[headerSize] = 0x20
	binary.LittleEndian.PutUint32(body[headerSize+1:headerSize+5], uint32(now.UnixMilli()))
	copy(body[headerSize+5:], frame)
	if err := sendLivePacket(opCtx, s.writer, body, "live talkback media"); err != nil {
		return talkFailure("audio send failed", err)
	}
	stream.next = time.Now().Add(time.Duration(int64(1024) * int64(time.Second) / int64(audio.rate)))
	return nil
}

// Close requests receive-only audio and releases the exclusive stream.
func (stream *TalkStream) Close(ctx context.Context) error {
	if ctx == nil {
		return errors.New("talkback context is required")
	}
	c := stream.owner
	// Cleanup is bounded independently of a canceled caller context.
	cleanupCtx, cancel := context.WithTimeout(stream.session.ctx, 2*connectTimeout)
	defer cancel()
	if err := c.lock(cleanupCtx); err != nil {
		_ = stream.session.writer.Close()
		return err
	}
	defer c.unlock()
	s := stream.session
	if stream.closed {
		return nil
	}
	stream.end(ErrTalkClosed)
	if c.session != s || s.ctx.Err() != nil {
		return nil
	}
	s.active = nil
	err := liveTalkCommand(cleanupCtx, s, sendLiveAudioDirection)
	if err != nil {
		return err
	}
	return ctx.Err()
}

func liveTalkCommand(ctx context.Context, s *liveTalkSession, send func(context.Context, *sessionWriter) error) error {
	opCtx, cancel := context.WithCancel(ctx)
	stop := context.AfterFunc(s.ctx, cancel)
	defer func() { stop(); cancel() }()
	if err := send(opCtx, s.writer); err != nil {
		return talkFailure("audio direction send failed", err)
	}
	return nil
}

func sendLiveTalkDirection(ctx context.Context, w *sessionWriter) error {
	packet, err := EncodeCommand([]map[string]any{{"Q": 5, "V": 1, "P": []int{3}}})
	if err != nil {
		return err
	}
	return sendLivePacket(ctx, w, packet, "talkback direction request")
}

func sendLiveAudioDirection(ctx context.Context, w *sessionWriter) error {
	packet, err := EncodeCommand([]map[string]any{{"Q": 5, "V": 1, "P": []int{1}}})
	if err != nil {
		return err
	}
	return sendLivePacket(ctx, w, packet, "audio direction request")
}

// Cancellation before the write does not break the shared video connection.
// Once a write has started, any failure may leave a partial packet.
func sendLivePacket(ctx context.Context, w *sessionWriter, packet []byte, operation string) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	deadline := time.Now().Add(connectTimeout)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	if err := w.SetWriteDeadline(deadline); err != nil {
		return err
	}
	finished := make(chan struct{})
	stop := context.AfterFunc(ctx, func() {
		_ = w.SetWriteDeadline(time.Now())
		close(finished)
	})
	if err := ctx.Err(); err != nil {
		if !stop() {
			<-finished
		}
		return err
	}
	err := writeFull(w.Conn, packet)
	if !stop() {
		<-finished
	}
	if err != nil {
		_ = w.Close()
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return fmt.Errorf("send %s: %w", operation, err)
	}
	return nil
}
