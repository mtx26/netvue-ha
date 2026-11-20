package manager

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue"
	"github.com/pion/rtp"
)

type talkHarness struct {
	c        *talkController
	cancel   context.CancelFunc
	messages chan talkMessage
	opens    atomic.Int32
	closes   atomic.Int32
	muted    atomic.Bool
	openErr  atomic.Value // error
	openGate chan struct{}
	done     chan struct{}
}

type fakeSink struct{ h *talkHarness }

func (fakeSink) AudioFormat() (int, int)                 { return 8000, 1 }
func (fakeSink) WriteADTS(context.Context, []byte) error { return nil }
func (s fakeSink) Close(context.Context) error           { s.h.closes.Add(1); return nil }
func (fakeSink) Done() <-chan struct{}                   { return nil }
func (fakeSink) Err() error                              { return nil }

func newTalkHarness(t *testing.T) *talkHarness {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	h := &talkHarness{cancel: cancel, messages: make(chan talkMessage, 64), done: make(chan struct{})}
	var wg sync.WaitGroup
	h.c = newTalkController(ctx,
		func(ctx context.Context) (talkSink, error) {
			if h.openGate != nil {
				<-h.openGate
			}
			if err, _ := h.openErr.Load().(error); err != nil {
				return nil, err
			}
			h.opens.Add(1)
			return fakeSink{h}, nil
		},
		func(ctx context.Context, source rtpAudioReader, _ talkSink) error {
			for {
				if _, err := source.ReadRTP(ctx); err != nil {
					return nil
				}
			}
		},
		h.muted.Store,
		func(f func()) bool { wg.Add(1); go func() { defer wg.Done(); f() }(); return true })
	h.c.output = func(m talkMessage) { h.messages <- m }
	go func() { h.c.run(); close(h.done) }()
	t.Cleanup(func() {
		cancel()
		select {
		case <-h.done:
		case <-time.After(5 * time.Second):
			t.Error("talk controller did not stop")
		}
		wg.Wait()
	})
	return h
}

func (h *talkHarness) expect(t *testing.T, kind, state, code string, id uint64) {
	t.Helper()
	select {
	case m := <-h.messages:
		if m.Type != kind || m.State != state || m.Code != code || m.ID != id {
			t.Fatalf("got %+v, want %s/%s/%s/%d", m, kind, state, code, id)
		}
	case <-time.After(5 * time.Second):
		t.Fatalf("no message %s/%s/%s/%d", kind, state, code, id)
	}
}

func (h *talkHarness) cmd(kind string, id uint64) { h.c.command(talkCommand{kind: kind, id: id}) }

func TestTalkPressReleaseCycles(t *testing.T) {
	h := newTalkHarness(t)
	for id := uint64(1); id <= 3; id++ {
		h.cmd("start", id)
		h.expect(t, "state", talkStateOpening, "", id)
		h.expect(t, "state", talkStateSpeaking, "", id)
		if !h.muted.Load() {
			t.Fatal("camera audio not muted while speaking")
		}
		h.cmd("stop", id)
		h.expect(t, "state", talkStateListening, "", id)
		if h.muted.Load() {
			t.Fatal("camera audio not restored after release")
		}
	}
	if h.opens.Load() != 3 || h.closes.Load() != 3 {
		t.Fatalf("opens=%d closes=%d", h.opens.Load(), h.closes.Load())
	}
}

func TestTalkStaleAndReplayedIDsIgnored(t *testing.T) {
	h := newTalkHarness(t)
	h.cmd("start", 5)
	h.expect(t, "state", talkStateOpening, "", 5)
	h.expect(t, "state", talkStateSpeaking, "", 5)
	h.cmd("stop", 5)
	h.expect(t, "state", talkStateListening, "", 5)
	h.cmd("start", 4)
	h.cmd("start", 5)
	time.Sleep(100 * time.Millisecond)
	if h.opens.Load() != 1 || len(h.messages) != 0 {
		t.Fatalf("stale start reactivated the microphone: opens=%d", h.opens.Load())
	}
}

func TestTalkReleaseDuringOpenStillClosesSink(t *testing.T) {
	h := newTalkHarness(t)
	h.openGate = make(chan struct{})
	h.cmd("start", 1)
	h.expect(t, "state", talkStateOpening, "", 1)
	h.cmd("stop", 1)
	close(h.openGate)
	h.expect(t, "state", talkStateListening, "", 1)
	if h.opens.Load() != h.closes.Load() {
		t.Fatalf("leaked sink: opens=%d closes=%d", h.opens.Load(), h.closes.Load())
	}
	if h.muted.Load() {
		t.Fatal("camera audio left muted")
	}
}

func TestTalkOpenFailureReportsAndRecovers(t *testing.T) {
	h := newTalkHarness(t)
	h.openErr.Store(netvue.ErrTalkBusy)
	h.cmd("start", 1)
	h.expect(t, "state", talkStateOpening, "", 1)
	h.expect(t, "error", "", talkErrBusy, 1)
	h.expect(t, "state", talkStateListening, "", 1)
	if h.muted.Load() {
		t.Fatal("camera audio left muted after failure")
	}
}

func TestTalkKeepaliveTimeoutAndMaxDuration(t *testing.T) {
	oldKeep, oldMax := talkKeepaliveTimeout, talkMaxDuration
	talkKeepaliveTimeout, talkMaxDuration = 150*time.Millisecond, 400*time.Millisecond
	t.Cleanup(func() { talkKeepaliveTimeout, talkMaxDuration = oldKeep, oldMax })

	h := newTalkHarness(t)
	h.cmd("start", 1)
	h.expect(t, "state", talkStateOpening, "", 1)
	h.expect(t, "state", talkStateSpeaking, "", 1)
	// No keepalive: the server releases by itself.
	h.expect(t, "error", "", talkErrTimeout, 1)
	h.expect(t, "state", talkStateListening, "", 1)

	h.cmd("start", 2)
	h.expect(t, "state", talkStateOpening, "", 2)
	h.expect(t, "state", talkStateSpeaking, "", 2)
	ticker := time.NewTicker(50 * time.Millisecond)
	defer ticker.Stop()
	stop := time.After(3 * time.Second)
	for {
		select {
		case <-ticker.C:
			h.cmd("keepalive", 2)
		case m := <-h.messages:
			if m.Type != "error" || m.Code != talkErrTimeout {
				t.Fatalf("unexpected %+v", m)
			}
			h.expect(t, "state", talkStateListening, "", 2)
			return
		case <-stop:
			t.Fatal("maximum press duration not enforced")
		}
	}
}

func TestTalkChannelCloseAndShutdownRelease(t *testing.T) {
	h := newTalkHarness(t)
	h.cmd("start", 1)
	h.expect(t, "state", talkStateOpening, "", 1)
	h.expect(t, "state", talkStateSpeaking, "", 1)
	h.cmd("release", 0)
	h.expect(t, "state", talkStateListening, "", 1)

	h.cmd("start", 2)
	h.expect(t, "state", talkStateOpening, "", 2)
	h.expect(t, "state", talkStateSpeaking, "", 2)
	h.cancel()
	<-h.done
	if h.opens.Load() != h.closes.Load() {
		t.Fatalf("sink leaked at shutdown: opens=%d closes=%d", h.opens.Load(), h.closes.Load())
	}
}

func TestTalkRejectsMalformedMessages(t *testing.T) {
	h := newTalkHarness(t)
	for _, raw := range []string{`{`, `{"t":"start"}`, `{"t":"start","id":0}`, `{"t":"bogus","id":1}`, string(make([]byte, 1000))} {
		h.c.handleMessage([]byte(raw))
	}
	time.Sleep(100 * time.Millisecond)
	if h.opens.Load() != 0 || len(h.messages) != 0 {
		t.Fatal("malformed message had an effect")
	}
}

func TestTalkDeliverDropsPacketsOutsideAPress(t *testing.T) {
	h := newTalkHarness(t)
	h.c.deliver(&rtp.Packet{})
	if h.c.active != nil {
		t.Fatal("packet created a press")
	}
}

func TestAudioGateKeepsSequenceContiguous(t *testing.T) {
	var got []uint16
	gate := newAudioGate(rtpWriterFunc(func(p *rtp.Packet) error { got = append(got, p.SequenceNumber); return nil }))
	send := func(seq uint16) { _ = gate.WriteRTP(&rtp.Packet{Header: rtp.Header{SequenceNumber: seq}}) }
	send(100)
	send(101)
	gate.setMuted(true)
	send(102)
	send(103)
	gate.setMuted(false)
	send(104)
	gate.restart()
	send(7)
	send(8)
	for i := 1; i < len(got); i++ {
		if got[i] != got[i-1]+1 {
			t.Fatalf("sequence gap: %v", got)
		}
	}
	if len(got) != 5 {
		t.Fatalf("muted packets leaked: %v", got)
	}
}

type rtpWriterFunc func(*rtp.Packet) error

func (f rtpWriterFunc) WriteRTP(p *rtp.Packet) error { return f(p) }
