package netvue

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"net"
	"testing"
	"time"
)

func readTalkPacket(t *testing.T, conn net.Conn) Packet {
	t.Helper()
	if err := conn.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	packet, err := ReadPacket(conn)
	if err != nil {
		t.Fatal(err)
	}
	return packet
}

func assertDirection(t *testing.T, packet Packet, direction int) {
	t.Helper()
	units, err := ParseUnits(packet.Body)
	if err != nil || packet.Kind != packetCommand || len(units) != 1 || !commandHasValues(units[0], 5, 1, []int{direction}) {
		t.Fatalf("direction %d: packet=%+v err=%v", direction, packet, err)
	}
}

type notifyingAudioTestPublisher struct {
	audioTestPublisher
	audioReceived chan audioFrame
}

func (publisher *notifyingAudioTestPublisher) PushAudio(frame audioFrame, parameters *audioParameters) error {
	if err := publisher.audioTestPublisher.PushAudio(frame, parameters); err != nil {
		return err
	}
	publisher.audioReceived <- frame
	return nil
}

func TestLiveTalkOnReceivingSession(t *testing.T) {
	client, server := testPipe(t)
	controller := NewLiveTalkback()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- receiveLiveWithTalkController(ctx, client, &audioTestPublisher{}, time.Second, 3*time.Second, true, nil, controller)
	}()
	if _, err := controller.Open(ctx); !errors.Is(err, ErrTalkUnavailable) {
		t.Fatalf("unready Open: %v", err)
	}
	ack := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 0, "P": [][]any{{1, 1, 2, []any{8000, 1, 1}}}}})
	if err := writeFull(server, ack); err != nil {
		t.Fatal(err)
	}
	assertDirection(t, readTalkPacket(t, server), 1)
	if err := writeFull(server, mediaPacket(0, []byte{1})); err != nil {
		t.Fatal(err)
	}
	// A later command is a barrier: the preceding video has been processed.
	barrier := mustEncodeCommand(t, []map[string]any{{"A": 99}})
	if err := writeFull(server, barrier); err != nil {
		t.Fatal(err)
	}
	type opened struct {
		stream *TalkStream
		err    error
	}
	openDone := make(chan opened, 1)
	go func() { stream, err := controller.Open(ctx); openDone <- opened{stream, err} }()
	assertDirection(t, readTalkPacket(t, server), 3)
	result := <-openDone
	if result.err != nil {
		t.Fatal(result.err)
	}
	stream := result.stream
	if _, err := controller.Open(ctx); !errors.Is(err, ErrTalkBusy) {
		t.Fatalf("second Open: %v", err)
	}
	// Repeated identical LIVE selections must not invalidate an active stream.
	if err := writeFull(server, ack); err != nil {
		t.Fatal(err)
	}
	if err := writeFull(server, barrier); err != nil {
		t.Fatal(err)
	}
	if err := stream.WriteADTS(ctx, testADTS(8, 1, 7)); !errors.Is(err, ErrUnsupportedAudioParameters) {
		t.Fatalf("mismatched rate: %v", err)
	}
	if err := stream.WriteADTS(ctx, []byte{1, 2}); !errors.Is(err, ErrAudioFrame) {
		t.Fatalf("invalid frame: %v", err)
	}
	frame := testADTS(11, 1, 7)
	wrote := make(chan error, 1)
	go func() { wrote <- stream.WriteADTS(ctx, frame) }()
	packet := readTalkPacket(t, server)
	if packet.Kind != packetMedia || packet.Body[0] != 0x20 || !bytes.Equal(packet.Body[5:], frame) ||
		int32(uint32(time.Now().UnixMilli())-binary.LittleEndian.Uint32(packet.Body[1:5])) > 1000 {
		t.Fatalf("invalid media: %+v", packet)
	}
	if err := <-wrote; err != nil {
		t.Fatal(err)
	}
	// A canceled write during pacing must not emit a frame or tear down video.
	short, shortCancel := context.WithTimeout(ctx, 10*time.Millisecond)
	defer shortCancel()
	if err := stream.WriteADTS(short, frame); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("paced cancellation: %v", err)
	}
	closed := make(chan error, 1)
	closeCtx, closeCancel := context.WithCancel(context.Background())
	closeCancel()
	go func() { closed <- stream.Close(closeCtx) }()
	assertDirection(t, readTalkPacket(t, server), 1)
	if err := <-closed; !errors.Is(err, context.Canceled) {
		t.Fatalf("close with canceled context: %v", err)
	}
	if err := stream.WriteADTS(ctx, frame); !errors.Is(err, ErrTalkClosed) {
		t.Fatalf("write after close: %v", err)
	}
	openDone = make(chan opened, 1)
	go func() { s, err := controller.Open(ctx); openDone <- opened{s, err} }()
	assertDirection(t, readTalkPacket(t, server), 3)
	again := <-openDone
	if again.err != nil {
		t.Fatalf("reopen after canceled close: %v", again.err)
	}
	go func() { closed <- again.stream.Close(ctx) }()
	assertDirection(t, readTalkPacket(t, server), 1)
	if err := <-closed; err != nil {
		t.Fatal(err)
	}
	if err := writeFull(server, mediaPacket(33, []byte{2})); err != nil {
		t.Fatalf("video after talk: %v", err)
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("session did not stop")
	}
	if _, err := controller.Open(context.Background()); !errors.Is(err, ErrTalkUnavailable) {
		t.Fatalf("open after session ended: %v", err)
	}
}

func TestLiveTalkDirectionRejectionDoesNotStopVideo(t *testing.T) {
	client, server := testPipe(t)
	controller := NewLiveTalkback()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- receiveLiveWithTalkController(ctx, client, &audioTestPublisher{}, time.Second, time.Second, true, nil, controller)
	}()
	ack := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 0, "P": [][]any{{1, 1, 2, []any{8000, 1, 1}}}}})
	if err := writeFull(server, ack); err != nil {
		t.Fatal(err)
	}
	assertDirection(t, readTalkPacket(t, server), 1)
	reject := mustEncodeCommand(t, []map[string]any{{"A": 5, "V": 1, "C": 1}})
	if err := writeFull(server, reject); err != nil {
		t.Fatal(err)
	}
	if err := writeFull(server, mediaPacket(0, []byte{1})); err != nil {
		t.Fatal(err)
	}
	barrier := mustEncodeCommand(t, []map[string]any{{"A": 99}})
	if err := writeFull(server, barrier); err != nil {
		t.Fatal(err)
	}
	if _, err := controller.Open(ctx); !errors.Is(err, ErrTalkUnavailable) {
		t.Fatalf("open after rejection: %v", err)
	}
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("session ended prematurely: %v", err)
	}
}

func TestLiveTalkExplicitRejectionSignalsStreamAndPreservesCameraAudioAndVideo(t *testing.T) {
	client, server := testPipe(t)
	controller := NewLiveTalkback()
	publisher := &notifyingAudioTestPublisher{audioReceived: make(chan audioFrame, 1)}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- receiveLiveWithTalkController(ctx, client, publisher, time.Second, time.Second, true, nil, controller)
	}()
	ack := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 0, "P": [][]any{{1, 1, 2, []any{8000, 1, 1}}}}})
	if err := writeFull(server, ack); err != nil {
		t.Fatal(err)
	}
	assertDirection(t, readTalkPacket(t, server), 1)
	if err := writeFull(server, mediaPacket(0, []byte{1})); err != nil {
		t.Fatal(err)
	}
	barrier := mustEncodeCommand(t, []map[string]any{{"A": 99}})
	if err := writeFull(server, barrier); err != nil {
		t.Fatal(err)
	}
	type openResult struct {
		stream *TalkStream
		err    error
	}
	opened := make(chan openResult, 1)
	go func() {
		stream, err := controller.Open(ctx)
		opened <- openResult{stream: stream, err: err}
	}()
	assertDirection(t, readTalkPacket(t, server), 3)
	result := <-opened
	if result.err != nil {
		t.Fatal(result.err)
	}
	stream := result.stream
	reject := mustEncodeCommand(t, []map[string]any{{"A": 5, "V": 1, "C": 1}})
	if err := writeFull(server, reject); err != nil {
		t.Fatal(err)
	}
	select {
	case <-stream.Done():
		if !errors.Is(stream.Err(), ErrTalkback) {
			t.Fatalf("rejection reason: %v", stream.Err())
		}
	case <-time.After(time.Second):
		t.Fatal("explicit rejection not propagated")
	}
	audio := testADTS(11, 1, 7)
	if err := writeFull(server, audioTestPacket(false, 40, audio)); err != nil {
		t.Fatalf("camera audio after talkback rejection: %v", err)
	}
	select {
	case frame := <-publisher.audioReceived:
		if frame.timestamp != 40 || !bytes.Equal(frame.payload, audio) {
			t.Fatalf("camera audio after talkback rejection = %+v, want timestamp 40 and original AAC payload", frame)
		}
	case <-time.After(time.Second):
		t.Fatal("camera receive audio stopped after talkback rejection")
	}
	if err := writeFull(server, mediaPacket(33, []byte{2})); err != nil {
		t.Fatalf("video stopped on talkback rejection: %v", err)
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("session did not stop")
	}
}

func TestLiveTalkCanceledWhileWaitingForWriter(t *testing.T) {
	client, server := testPipe(t)
	writer := &sessionWriter{Conn: client}
	writer.mu.Lock()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- sendLiveTalkDirection(ctx, writer) }()
	cancel()
	writer.mu.Unlock()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("send: %v", err)
	}
	packet := mustEncodeCommand(t, []map[string]any{{"A": 99}})
	written := make(chan error, 1)
	go func() { written <- writeFull(client, packet) }()
	if got := readTalkPacket(t, server); got.Kind != packetCommand {
		t.Fatalf("connection no longer usable: %+v", got)
	}
	if err := <-written; err != nil {
		t.Fatal(err)
	}
}

func TestLiveTalkCancellationDuringWriteClosesCorruptibleSession(t *testing.T) {
	client, server := testPipe(t)
	writer := &sessionWriter{Conn: client}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	// With no peer reader, net.Pipe blocks in Write until the deadline.
	if err := sendLiveTalkDirection(ctx, writer); err != nil {
		var networkErr net.Error
		if !errors.Is(err, context.DeadlineExceeded) && !(errors.As(err, &networkErr) && networkErr.Timeout()) {
			t.Fatalf("send: %v", err)
		}
	} else {
		t.Fatal("blocked write unexpectedly succeeded")
	}
	if _, err := server.Write([]byte{1}); err == nil {
		t.Fatal("connection should be closed after a potentially partial write")
	}
}

func TestLiveTalkChangedSelectionRestoresDirection(t *testing.T) {
	client, server := testPipe(t)
	controller := NewLiveTalkback()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		done <- receiveLiveWithTalkController(ctx, client, &audioTestPublisher{}, time.Second, time.Second, true, nil, controller)
	}()
	selection := func(rate int) []byte {
		return mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 0, "P": [][]any{{1, 1, 2, []any{rate, 1, 1}}}}})
	}
	if err := writeFull(server, selection(8000)); err != nil {
		t.Fatal(err)
	}
	assertDirection(t, readTalkPacket(t, server), 1)
	if err := writeFull(server, mediaPacket(0, []byte{1})); err != nil {
		t.Fatal(err)
	}
	barrier := mustEncodeCommand(t, []map[string]any{{"A": 99}})
	if err := writeFull(server, barrier); err != nil {
		t.Fatal(err)
	}
	type openResult struct {
		stream *TalkStream
		err    error
	}
	opened := make(chan openResult, 1)
	go func() {
		stream, err := controller.Open(ctx)
		opened <- openResult{stream: stream, err: err}
	}()
	assertDirection(t, readTalkPacket(t, server), 3)
	result := <-opened
	if result.err != nil {
		t.Fatal(result.err)
	}
	stream := result.stream
	if err := writeFull(server, selection(16000)); err != nil {
		t.Fatal(err)
	}
	assertDirection(t, readTalkPacket(t, server), 1)
	if err := writeFull(server, barrier); err != nil {
		t.Fatal(err)
	}
	if err := stream.WriteADTS(ctx, testADTS(11, 1, 7)); !errors.Is(err, ErrTalkClosed) {
		t.Fatalf("old stream after selection changed: %v", err)
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("session did not stop")
	}
}

func TestLiveTalkIsolationAndReconnect(t *testing.T) {
	first, firstPeer := testPipe(t)
	second, secondPeer := testPipe(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	a, b := NewLiveTalkback(), NewLiveTalkback()
	params := &audioParameters{sampleRate: 8000, channels: 1}
	a.attachIfAbsent(ctx, &sessionWriter{Conn: first}, params)
	b.attachIfAbsent(ctx, &sessionWriter{Conn: second}, params)
	type openResult struct {
		stream *TalkStream
		err    error
	}
	open := func(c *LiveTalkback, peer net.Conn) *TalkStream {
		done := make(chan openResult, 1)
		go func() {
			stream, err := c.Open(ctx)
			done <- openResult{stream: stream, err: err}
		}()
		assertDirection(t, readTalkPacket(t, peer), 3)
		result := <-done
		if result.err != nil {
			t.Fatalf("Open() failed: %v", result.err)
		}
		return result.stream
	}
	s1, s2 := open(a, firstPeer), open(b, secondPeer)
	a.detach()
	if err := s1.WriteADTS(ctx, testADTS(11, 1, 1)); !errors.Is(err, ErrTalkClosed) {
		t.Fatalf("old stream survived disconnect: %v", err)
	}
	a.attachIfAbsent(ctx, &sessionWriter{Conn: first}, params)
	s3 := open(a, firstPeer)
	if err := s1.WriteADTS(ctx, testADTS(11, 1, 1)); !errors.Is(err, ErrTalkClosed) {
		t.Fatalf("old stream survived reconnect: %v", err)
	}
	wrote := make(chan error, 1)
	go func() { wrote <- s2.WriteADTS(ctx, testADTS(11, 1, 2)) }()
	if packet := readTalkPacket(t, secondPeer); packet.Kind != packetMedia || packet.Body[5] != 0xff {
		t.Fatalf("second camera packet: %+v", packet)
	}
	if err := <-wrote; err != nil {
		t.Fatal(err)
	}
	a.detach()
	b.detach()
	if err := s3.Close(ctx); err != nil {
		t.Fatal(err)
	}
}

func TestLiveTalkCanceledBeforeOpenDoesNotWrite(t *testing.T) {
	client, server := testPipe(t)
	controller := NewLiveTalkback()
	controller.attachIfAbsent(context.Background(), &sessionWriter{Conn: client}, &audioParameters{sampleRate: 8000, channels: 1})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := controller.Open(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("open: %v", err)
	}
	if err := server.SetReadDeadline(time.Now().Add(30 * time.Millisecond)); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadPacket(server); err == nil {
		t.Fatal("unexpected write")
	} else {
		var nerr net.Error
		if !errors.As(err, &nerr) || !nerr.Timeout() {
			t.Fatalf("connection unexpectedly closed: %v", err)
		}
	}
	controller.detach()
}
