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

func TestAuthenticationRequiresExplicitResultAndSendsOneLoginRequest(t *testing.T) {
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
		{name: "null result", response: map[string]any{"A": 1, "V": 1, "C": nil}, wantErr: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			client, server := net.Pipe()
			defer client.Close()
			defer server.Close()
			camera := camera{host: "192.168.10.2", port: 5555, key: "fake-key"}
			serverResult := make(chan error, 1)
			go func() {
				request, err := ReadPacket(server)
				if err == nil && request.Kind != packetCommand {
					err = ErrProtocol
				}
				if err == nil {
					var units []map[string]json.RawMessage
					units, err = ParseUnits(request.Body)
					if err == nil && len(units) != 1 {
						err = ErrProtocol
					}
					if err == nil {
						var action, version int
						if json.Unmarshal(units[0]["Q"], &action) != nil || action != 1 ||
							json.Unmarshal(units[0]["V"], &version) != nil || version != 1 {
							err = ErrProtocol
						}
						var params []json.RawMessage
						if err == nil && (json.Unmarshal(units[0]["P"], &params) != nil || len(params) != 5) {
							err = ErrProtocol
						} else if err == nil {
							var key, username, host string
							var nonce int32
							var port int
							if json.Unmarshal(params[0], &key) != nil || key != camera.key ||
								json.Unmarshal(params[1], &nonce) != nil ||
								json.Unmarshal(params[2], &username) != nil || username != localUserName ||
								json.Unmarshal(params[3], &host) != nil || host != camera.host ||
								json.Unmarshal(params[4], &port) != nil || port != camera.port {
								err = ErrProtocol
							}
						}
					}
				}
				if err != nil {
					serverResult <- err
					return
				}

				response, err := EncodeCommand([]map[string]any{test.response})
				if err == nil {
					err = writeFull(server, response)
				}
				if err == nil {
					_, err = ReadPacket(server)
					if err == nil {
						err = errors.New("authentication sent an extra request")
					} else if errors.Is(err, ErrConnectionClosed) {
						err = nil
					}
				}
				serverResult <- err
			}()
			got, err := authenticate(context.Background(), client, camera, time.Second)
			_ = client.Close()
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
