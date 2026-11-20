package manager

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

// Push-to-talk is driven by an explicit control channel carried by the same
// WebRTC connection as the video. Microphone RTP packets never decide whether
// the user is speaking; they are only consumed while a press is active.
const (
	talkChannelLabel   = "netvue-ptt"
	talkPacketQueue    = 32
	talkMaxMessageSize = 256
)

// Variables so that tests can shorten them.
var (
	talkKeepaliveTimeout = 2 * time.Second
	talkMaxDuration      = time.Minute
	talkOpenTimeout      = 5 * time.Second
	talkCloseTimeout     = 5 * time.Second
)

const (
	talkErrBusy        = "busy"
	talkErrUnavailable = "unavailable"
	talkErrFailed      = "failed"
	talkErrTimeout     = "timeout"
	talkErrUnsupported = "unsupported"
)

const (
	talkStateListening = "listening"
	talkStateOpening   = "opening"
	talkStateSpeaking  = "speaking"
)

type talkMessage struct {
	Type  string `json:"t"`
	ID    uint64 `json:"id"`
	State string `json:"state,omitempty"`
	Code  string `json:"code,omitempty"`
}

type talkCommand struct {
	kind string
	id   uint64
}

// talkSegment is one press of the button. The camera speaking channel is held
// from a successful open until sink.Close has returned, even after release.
type talkSegment struct {
	id       uint64
	cancel   context.CancelFunc
	packets  chan *rtp.Packet
	opened   bool
	stopping bool
}

type talkEvent struct {
	segment *talkSegment
	opened  bool
	ended   bool
	err     error
}

// talkController owns the speaking/listening state of one WebRTC peer.
// Everything except deliver, setChannel and command runs on the run goroutine.
type talkController struct {
	ctx     context.Context
	open    func(context.Context) (talkSink, error)
	convert func(context.Context, rtpAudioReader, talkSink) error
	mute    func(bool)
	spawn   func(func()) bool

	cmds   chan talkCommand
	events chan talkEvent

	// output replaces the data channel when set (tests).
	output  func(talkMessage)
	mu      sync.Mutex
	channel *webrtc.DataChannel
	active  *talkSegment
	claimed bool
	opus    bool
}

func newTalkController(
	ctx context.Context,
	open func(context.Context) (talkSink, error),
	convert func(context.Context, rtpAudioReader, talkSink) error,
	mute func(bool),
	spawn func(func()) bool,
) *talkController {
	return &talkController{
		ctx: ctx, open: open, convert: convert, mute: mute, spawn: spawn,
		cmds: make(chan talkCommand), events: make(chan talkEvent), opus: true,
	}
}

func (c *talkController) claimChannel() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.claimed {
		return false
	}
	c.claimed = true
	return true
}

func (c *talkController) setChannel(dc *webrtc.DataChannel) {
	c.mu.Lock()
	c.channel = dc
	c.mu.Unlock()
}

func (c *talkController) send(message talkMessage) {
	if c.output != nil {
		c.output(message)
		return
	}
	c.mu.Lock()
	dc := c.channel
	c.mu.Unlock()
	if dc == nil || dc.ReadyState() != webrtc.DataChannelStateOpen {
		return
	}
	data, err := json.Marshal(message)
	if err == nil {
		_ = dc.SendText(string(data))
	}
}

func (c *talkController) command(cmd talkCommand) {
	select {
	case c.cmds <- cmd:
	case <-c.ctx.Done():
	}
}

// handleMessage validates untrusted control messages from the browser.
func (c *talkController) handleMessage(data []byte) {
	if len(data) > talkMaxMessageSize {
		return
	}
	var message talkMessage
	if json.Unmarshal(data, &message) != nil || message.ID == 0 {
		return
	}
	switch message.Type {
	case "start", "stop", "keepalive":
		c.command(talkCommand{kind: message.Type, id: message.ID})
	}
}

func (c *talkController) setActive(segment *talkSegment) {
	c.mu.Lock()
	c.active = segment
	c.mu.Unlock()
}

func (c *talkController) setUnsupportedCodec() {
	c.mu.Lock()
	c.opus = false
	c.mu.Unlock()
}

func (c *talkController) supported() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.opus
}

// deliver hands one microphone packet to the current press, or drops it.
func (c *talkController) deliver(packet *rtp.Packet) {
	c.mu.Lock()
	segment := c.active
	c.mu.Unlock()
	if segment == nil {
		return
	}
	select {
	case segment.packets <- packet:
	default:
	}
}

func (c *talkController) run() {
	var (
		lastID  uint64
		current *talkSegment
		pending uint64
		failure string
	)
	keepalive, maxTimer := time.NewTimer(time.Hour), time.NewTimer(time.Hour)
	stopTimer := func(t *time.Timer) {
		if !t.Stop() {
			select {
			case <-t.C:
			default:
			}
		}
	}
	stopTimer(keepalive)
	stopTimer(maxTimer)
	defer keepalive.Stop()
	defer maxTimer.Stop()

	state := func(id uint64, s string) { c.send(talkMessage{Type: "state", State: s, ID: id}) }
	stop := func(code string) {
		if current == nil || current.stopping {
			return
		}
		current.stopping = true
		failure = code
		c.setActive(nil)
		current.cancel()
		stopTimer(keepalive)
		stopTimer(maxTimer)
	}
	begin := func(id uint64) {
		if !c.supported() {
			c.send(talkMessage{Type: "error", Code: talkErrUnsupported, ID: id})
			c.mute(false)
			state(id, talkStateListening)
			return
		}
		ctx, cancel := context.WithCancel(c.ctx)
		current = &talkSegment{id: id, cancel: cancel, packets: make(chan *rtp.Packet, talkPacketQueue)}
		failure = ""
		c.setActive(current)
		keepalive.Reset(talkKeepaliveTimeout)
		maxTimer.Reset(talkMaxDuration)
		state(id, talkStateOpening)
		segment := current
		if !c.spawn(func() { c.runSegment(ctx, segment) }) {
			cancel()
			current = nil
			c.setActive(nil)
			c.mute(false)
		}
	}

	for {
		select {
		case <-c.ctx.Done():
			stop("")
			for current != nil {
				if event := <-c.events; event.ended && event.segment == current {
					current = nil
				}
			}
			return
		case cmd := <-c.cmds:
			switch cmd.kind {
			case "start":
				if cmd.id <= lastID {
					continue
				}
				lastID = cmd.id
				// Camera audio is muted as soon as the press is accepted.
				c.mute(true)
				if current != nil {
					stop("")
					pending = cmd.id
				} else {
					begin(cmd.id)
				}
			case "stop":
				if current != nil && current.id == cmd.id {
					stop("")
				} else if pending == cmd.id {
					pending = 0
				}
			case "keepalive":
				if current != nil && !current.stopping && current.id == cmd.id {
					stopTimer(keepalive)
					keepalive.Reset(talkKeepaliveTimeout)
				}
			case "release":
				pending = 0
				stop("")
			}
		case <-keepalive.C:
			slog.Warn("Netvue push-to-talk keepalive lost")
			stop(talkErrTimeout)
		case <-maxTimer.C:
			slog.Warn("Netvue push-to-talk maximum duration reached")
			stop(talkErrTimeout)
		case event := <-c.events:
			if event.segment != current {
				continue
			}
			if event.opened {
				if !current.stopping {
					current.opened = true
					state(current.id, talkStateSpeaking)
				}
				continue
			}
			id, code := current.id, failure
			if code == "" && event.err != nil && !current.stopping {
				code = classifyTalkError(event.err)
				slog.Warn("Netvue push-to-talk ended", "code", code, "error", event.err)
			} else if event.err != nil && !errors.Is(event.err, context.Canceled) {
				slog.Warn("Netvue push-to-talk cleanup failed", "error", event.err)
			}
			current = nil
			stopTimer(keepalive)
			stopTimer(maxTimer)
			if code != "" {
				c.send(talkMessage{Type: "error", Code: code, ID: id})
			}
			if pending != 0 {
				next := pending
				pending = 0
				begin(next)
				continue
			}
			c.mute(false)
			state(id, talkStateListening)
		}
	}
}

func classifyTalkError(err error) string {
	switch {
	case errors.Is(err, netvue.ErrTalkBusy):
		return talkErrBusy
	case errors.Is(err, netvue.ErrTalkUnavailable), errors.Is(err, netvue.ErrTalkClosed),
		errors.Is(err, ErrCameraNotFound), errors.Is(err, ErrManagerClosed):
		return talkErrUnavailable
	case errors.Is(err, context.DeadlineExceeded):
		return talkErrTimeout
	default:
		return talkErrFailed
	}
}

func (c *talkController) report(event talkEvent) {
	// run keeps receiving until the current segment has ended.
	c.events <- event
}

func (c *talkController) runSegment(ctx context.Context, segment *talkSegment) {
	openCtx, cancelOpen := context.WithTimeout(c.ctx, talkOpenTimeout)
	// The open command is never aborted by a release: cancelling a camera
	// write could cut the shared video connection.
	sink, err := c.open(openCtx)
	cancelOpen()
	if err != nil {
		c.report(talkEvent{segment: segment, ended: true, err: err})
		return
	}
	var convertErr error
	if ctx.Err() == nil {
		c.report(talkEvent{segment: segment, opened: true})
		sinkDied := make(chan struct{})
		watched := make(chan struct{})
		go func() {
			defer close(watched)
			select {
			case <-sink.Done():
				close(sinkDied)
				segment.cancel()
			case <-ctx.Done():
			}
		}()
		convertErr = c.convert(ctx, &talkSource{packets: segment.packets}, sink)
		segment.cancel()
		<-watched
		select {
		case <-sinkDied:
			convertErr = fmt.Errorf("%w: %v", netvue.ErrTalkClosed, convertErr)
		default:
		}
	}
	closeCtx, cancelClose := context.WithTimeout(context.WithoutCancel(c.ctx), talkCloseTimeout)
	closeErr := sink.Close(closeCtx)
	cancelClose()
	err = convertErr
	if closeErr != nil {
		err = errors.Join(err, closeErr)
		if !errors.Is(closeErr, netvue.ErrTalkClosed) {
			err = errors.Join(err, netvue.ErrTalkUnavailable)
		}
	}
	c.report(talkEvent{segment: segment, ended: true, err: err})
}

type talkSource struct{ packets <-chan *rtp.Packet }

func (s *talkSource) ReadRTP(ctx context.Context) (*rtp.Packet, error) {
	select {
	case packet := <-s.packets:
		return packet, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}
