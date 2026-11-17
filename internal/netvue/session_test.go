package netvue

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"net"
	"testing"
	"time"
)

func TestLiveRequestIsOneExplicitCommand(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	result := make(chan error, 1)
	go func() {
		packet, err := ReadPacket(server)
		if err == nil && packet.Kind != packetCommand {
			err = ErrProtocol
		}
		if err == nil {
			var units []map[string]json.RawMessage
			err = json.Unmarshal(packet.Body, &units)
			if err == nil && len(units) != 1 {
				err = ErrProtocol
			}
			if err == nil {
				var command, version int
				if json.Unmarshal(units[0]["Q"], &command) != nil ||
					json.Unmarshal(units[0]["V"], &version) != nil ||
					command != 3 || version != 2 {
					err = ErrProtocol
				}
			}
		}
		result <- err
	}()
	if err := sendLiveRequest(context.Background(), client); err != nil {
		t.Fatal(err)
	}
	if err := <-result; err != nil {
		t.Fatal(err)
	}
}

func TestReceiveLiveRejectsMediaBeforeConfirmation(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	publisher, err := newVideoPublisher(context.Background(), "rtsp://127.0.0.1:8554/netvue")
	if err != nil {
		t.Fatal(err)
	}
	defer publisher.Close()
	go func() {
		_ = writeFull(server, makePacket(6, 0, []byte{0x10, 0, 0, 0, 0, 0x67}))
	}()
	err = receiveLive(contextWithTimeout(t), client, publisher)
	if err == nil || !errors.Is(err, ErrProtocol) {
		t.Fatalf("expected media-before-confirmation error, got %v", err)
	}
}

func contextWithTimeout(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	t.Cleanup(cancel)
	return ctx
}

func TestReceiveLiveStartupDeadlineIsNotExtendedByCommands(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	command, err := EncodeCommand([]map[string]any{{"A": 2, "V": 2, "C": 0}})
	if err != nil {
		t.Fatal(err)
	}
	go func() {
		ticker := time.NewTicker(10 * time.Millisecond)
		defer ticker.Stop()
		for range ticker.C {
			if err := writeFull(server, command); err != nil {
				return
			}
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	started := time.Now()
	err = receiveLiveWithTimeouts(ctx, client, &generationTestPublisher{},
		80*time.Millisecond, time.Second)
	if !errors.Is(err, ErrVideoNotReady) {
		t.Fatalf("expected absolute video startup timeout, got %v", err)
	}
	if elapsed := time.Since(started); elapsed > 500*time.Millisecond {
		t.Fatalf("commands prolonged video startup for %v", elapsed)
	}
}

func TestReceiveLiveCancellationWakesBlockedRead(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	publisher := &generationTestPublisher{total: 1, current: 1, generation: 1, pushed: make(chan struct{})}
	go func() {
		acknowledgment, _ := EncodeCommand([]map[string]any{{"A": 3, "V": 2, "C": 0}})
		_ = writeFull(server, acknowledgment)
		_ = writeFull(server, mediaPacket(1, []byte{1}))
	}()
	result := make(chan error, 1)
	go func() {
		result <- receiveLive(ctx, client, publisher)
	}()
	select {
	case <-publisher.pushed:
	case <-time.After(time.Second):
		t.Fatal("live frame was not processed")
	}
	cancel()
	select {
	case err := <-result:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("expected caller cancellation, got %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("blocked LIVE read did not stop after cancellation")
	}
}

func TestReceiveLiveHandlesCancellationRaceWithReadDeadline(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	conn := &readDeadlineRaceConn{
		Conn:    client,
		entered: make(chan struct{}),
		release: make(chan struct{}),
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() {
		result <- receiveLive(ctx, conn, &generationTestPublisher{})
	}()
	<-conn.entered
	cancel()
	close(conn.release)
	select {
	case err := <-result:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("expected cancellation after read-deadline race, got %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("receive loop did not stop after cancellation")
	}
}

func TestReceiveLiveDoesNotConvertParentDeadlineToSuccess(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	publisher := &generationTestPublisher{total: 1, current: 1, generation: 1}
	go func() {
		acknowledgment, _ := EncodeCommand([]map[string]any{{"A": 3, "V": 2, "C": 0}})
		_ = writeFull(server, acknowledgment)
		_ = writeFull(server, mediaPacket(1, []byte{1}))
	}()
	err := receiveLive(ctx, client, publisher)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected caller deadline error, got %v", err)
	}
}

func TestReceiveLiveReclassifiesPushErrorWhenContextEnds(t *testing.T) {
	tests := []struct {
		name        string
		cause       error
		wantErr     error
		wantSuccess bool
	}{
		{name: "caller cancellation", cause: context.Canceled, wantErr: context.Canceled},
		{name: "session duration", cause: errSessionDurationElapsed, wantSuccess: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			client, server := net.Pipe()
			defer client.Close()
			defer server.Close()
			ctx, cancel := context.WithCancelCause(context.Background())
			defer cancel(context.Canceled)
			publisher := &blockedPushPublisher{
				entered: make(chan struct{}),
				release: make(chan struct{}),
			}
			go func() {
				acknowledgment, _ := EncodeCommand([]map[string]any{{"A": 3, "V": 2, "C": 0}})
				_ = writeFull(server, acknowledgment)
				_ = writeFull(server, mediaPacket(1, []byte{1}))
				_ = writeFull(server, mediaPacket(2, []byte{2}))
			}()
			result := make(chan error, 1)
			go func() {
				result <- receiveLive(ctx, client, publisher)
			}()
			select {
			case <-publisher.entered:
			case <-time.After(time.Second):
				t.Fatal("publisher did not enter the blocked write")
			}
			cancel(test.cause)
			close(publisher.release)
			select {
			case err := <-result:
				if test.wantSuccess && err != nil {
					t.Fatalf("expected clean session end, got %v", err)
				}
				if test.wantErr != nil && !errors.Is(err, test.wantErr) {
					t.Fatalf("expected %v, got %v", test.wantErr, err)
				}
			case <-time.After(time.Second):
				t.Fatal("receive loop did not stop after context ended")
			}
		})
	}
}

func TestReceiveLiveRejectsInactivePublicationAtSessionEnd(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	publisher := &generationTestPublisher{resetOnly: true}
	go func() {
		acknowledgment, _ := EncodeCommand([]map[string]any{{"A": 3, "V": 2, "C": 0}})
		_ = writeFull(server, acknowledgment)
		_ = writeFull(server, mediaPacket(1, []byte{1}))
		_ = writeFull(server, mediaPacket(2, []byte{2}))
	}()
	ctx, cancel := context.WithTimeoutCause(context.Background(), 100*time.Millisecond, errSessionDurationElapsed)
	defer cancel()
	err := receiveLive(ctx, client, publisher)
	if !errors.Is(err, ErrVideoStartup) {
		t.Fatalf("expected inactive-publication startup error, got %v", err)
	}
	if publisher.PublishedFrames() != 5 || publisher.CurrentPublicationFrames() != 0 {
		t.Fatalf("unexpected publication generations: total=%d current=%d",
			publisher.PublishedFrames(), publisher.CurrentPublicationFrames())
	}
}

func TestReceiveLiveTracksNewPublicationGeneration(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	publisher := &generationTestPublisher{}
	go func() {
		acknowledgment, _ := EncodeCommand([]map[string]any{{"A": 3, "V": 2, "C": 0}})
		_ = writeFull(server, acknowledgment)
		_ = writeFull(server, mediaPacket(1, []byte{1}))
		_ = writeFull(server, mediaPacket(2, []byte{2}))
		_ = writeFull(server, mediaPacket(3, []byte{3}))
	}()
	ctx, cancel := context.WithTimeoutCause(context.Background(), 100*time.Millisecond, errSessionDurationElapsed)
	defer cancel()
	if err := receiveLive(ctx, client, publisher); err != nil {
		t.Fatalf("new publication generation was treated as silent: %v", err)
	}
	if publisher.Generation() != 2 || publisher.CurrentPublicationFrames() != 1 {
		t.Fatalf("unexpected publication generation: generation=%d current=%d",
			publisher.Generation(), publisher.CurrentPublicationFrames())
	}
}

type generationTestPublisher struct {
	total      uint64
	current    uint64
	generation uint64
	resetOnly  bool
	pushed     chan struct{}
}

type blockedPushPublisher struct {
	generationTestPublisher
	entered chan struct{}
	release chan struct{}
}

func (publisher *blockedPushPublisher) Push(accessUnit []byte, timestamp uint32) error {
	if accessUnit[0] == 2 {
		close(publisher.entered)
		<-publisher.release
		return net.ErrClosed
	}
	return publisher.generationTestPublisher.Push(accessUnit, timestamp)
}

type readDeadlineRaceConn struct {
	net.Conn
	entered chan struct{}
	release chan struct{}
}

func (conn *readDeadlineRaceConn) SetReadDeadline(time.Time) error {
	close(conn.entered)
	<-conn.release
	return net.ErrClosed
}

func (publisher *generationTestPublisher) Push(accessUnit []byte, _ uint32) error {
	switch accessUnit[0] {
	case 1:
		publisher.generation = 1
		publisher.total = 5
		publisher.current = 5
	case 2:
		if publisher.resetOnly {
			publisher.current = 0
		} else {
			publisher.generation++
			publisher.total++
			publisher.current = 1
		}
	case 3:
	}
	if publisher.pushed != nil {
		select {
		case <-publisher.pushed:
		default:
			close(publisher.pushed)
		}
	}
	return nil
}

func (publisher *generationTestPublisher) SetStartupDeadline(time.Time) {}

func (publisher *generationTestPublisher) PublishedFrames() uint64 {
	return publisher.total
}

func (publisher *generationTestPublisher) CurrentPublicationFrames() uint64 {
	return publisher.current
}

func (publisher *generationTestPublisher) Generation() uint64 {
	return publisher.generation
}

func mediaPacket(timestamp uint32, accessUnit []byte) []byte {
	body := make([]byte, 5+len(accessUnit))
	body[0] = 0x10
	binary.LittleEndian.PutUint32(body[1:5], timestamp)
	copy(body[5:], accessUnit)
	return makePacketKind(uint32(len(body)), 0, packetMedia, body)
}
