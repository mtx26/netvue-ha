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

func TestAuthenticationRequiresExplicitResultAndSendsOnce(t *testing.T) {
	for _, test := range []struct {
		name     string
		response map[string]any
		result   int64
		wantErr  bool
	}{
		{name: "accepted", response: map[string]any{"A": 1, "V": 1, "C": 0}, result: 0},
		{name: "rejected", response: map[string]any{"A": 1, "V": 2, "C": 5}, result: 5},
		{name: "missing result", response: map[string]any{"A": 1, "V": 1}, wantErr: true},
		{name: "invalid result type", response: map[string]any{"A": 1, "V": 1, "C": "0"}, wantErr: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			client, server := net.Pipe()
			defer client.Close()
			defer server.Close()
			camera := camera{host: "192.168.10.2", port: 5555, key: "fake-key"}
			serverResult := make(chan error, 1)
			go func() {
				request, err := ReadPacket(server)
				if err != nil {
					serverResult <- err
					return
				}
				if request.Kind != packetCommand {
					serverResult <- ErrProtocol
					return
				}
				units, err := ParseUnits(request.Body)
				if err != nil {
					serverResult <- err
					return
				}
				if len(units) != 1 {
					serverResult <- ErrProtocol
					return
				}
				paramsField, ok := units[0]["P"]
				if !ok {
					serverResult <- ErrProtocol
					return
				}
				var params []json.RawMessage
				if err := json.Unmarshal(paramsField, &params); err != nil || len(params) != 5 {
					serverResult <- ErrProtocol
					return
				}
				var sentKey string
				if json.Unmarshal(params[0], &sentKey) != nil || sentKey != camera.key {
					serverResult <- ErrProtocol
					return
				}
				response, err := EncodeCommand([]map[string]any{test.response})
				if err == nil {
					err = writeFull(server, response)
				}
				serverResult <- err
			}()
			got, err := authenticate(context.Background(), client, camera, time.Second)
			if err != nil && !test.wantErr {
				t.Fatal(err)
			}
			if err == nil && test.wantErr {
				t.Fatal("expected authentication protocol error")
			}
			if err == nil && got != test.result {
				t.Fatalf("got result %d, want %d", got, test.result)
			}
			if serverErr := <-serverResult; serverErr != nil {
				t.Fatal(serverErr)
			}
		})
	}
}

func TestAuthenticationRejectsNonCommandResponse(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	go func() {
		if _, err := ReadPacket(server); err != nil {
			return
		}
		response := makePacket(1, 0, []byte("x"))
		binary.LittleEndian.PutUint16(response[14:16], packetMedia)
		_ = writeFull(server, response)
	}()
	_, err := authenticate(context.Background(), client,
		camera{host: "192.168.10.2", port: 5555, key: "fake"}, time.Second)
	if !errors.Is(err, ErrProtocol) {
		t.Fatalf("expected ErrProtocol, got %v", err)
	}
}

func TestAuthenticationStopsWhenContextExpires(t *testing.T) {
	client, server := net.Pipe()
	defer client.Close()
	defer server.Close()
	serverResult := make(chan error, 1)
	go func() {
		if _, err := ReadPacket(server); err != nil {
			serverResult <- err
			return
		}
		_, err := ReadPacket(server)
		serverResult <- err
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	_, err := authenticate(ctx, client,
		camera{host: "192.168.10.2", port: 5555, key: "fake-key"}, time.Second)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected context deadline, got %v", err)
	}
	if err := <-serverResult; !errors.Is(err, ErrConnectionClosed) {
		t.Fatalf("expected connection close after cancellation, got %v", err)
	}
}
