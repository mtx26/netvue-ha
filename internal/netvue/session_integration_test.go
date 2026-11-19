package netvue

import (
	"context"
	"errors"
	"fmt"
	"net"
	"testing"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue/netvuetest"
)

func TestRunSessionStopsAfterRejectedAuthentication(t *testing.T) {
	tests := []struct {
		name       string
		result     any
		wantError  error
		diagnostic string
	}{
		{name: "rejected", result: 5, wantError: ErrAuthenticationRejected, diagnostic: "Camera authentication rejected [authentication]"},
		{name: "null", result: nil, wantError: ErrProtocol, diagnostic: "Invalid Netvue protocol data [authentication]: missing or invalid authentication result"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			testRunSessionStopsAfterAuthentication(t, test.result, test.wantError, test.diagnostic)
		})
	}
}

func testRunSessionStopsAfterAuthentication(t *testing.T, result any, wantError error, diagnostic string) {
	t.Helper()
	sourceIP := net.ParseIP("127.0.0.1").To4()
	targetIP := net.ParseIP("127.0.0.2").To4()
	udp, err := net.ListenUDP("udp4", &net.UDPAddr{IP: targetIP, Port: discoveryPort})
	if err != nil {
		netvuetest.RequireIntegration(t, "loopback discovery port is unavailable: %v", err)
		return
	}
	defer udp.Close()
	tcp, err := net.ListenTCP("tcp4", &net.TCPAddr{IP: targetIP})
	if err != nil {
		netvuetest.RequireIntegration(t, "secondary loopback address is unavailable: %v", err)
		return
	}
	defer tcp.Close()
	rtspListener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer rtspListener.Close()
	rtspURL := fmt.Sprintf("rtsp://127.0.0.1:%d/netvue", rtspListener.Addr().(*net.TCPAddr).Port)

	outcome := make(chan error, 1)
	go func() {
		if err := udp.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
			outcome <- err
			return
		}
		buffer := make([]byte, 65535)
		n, remote, err := udp.ReadFromUDP(buffer)
		if err != nil {
			outcome <- err
			return
		}
		request, err := ParseDatagram(buffer[:n])
		if err != nil || request.Kind != packetCommand {
			outcome <- ErrProtocol
			return
		}
		response, err := EncodeCommand([]map[string]any{{
			"A": 8, "V": 2, "C": 0,
			"P": []any{"fake-id", "fake-fw", targetIP.String(), tcp.Addr().(*net.TCPAddr).Port, "fake-key"},
		}})
		if err != nil {
			outcome <- err
			return
		}
		if _, err := udp.WriteToUDP(response, remote); err != nil {
			outcome <- err
			return
		}

		connection, err := tcp.AcceptTCP()
		if err != nil {
			outcome <- err
			return
		}
		defer connection.Close()
		if err := connection.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
			outcome <- err
			return
		}
		if _, err := ReadPacket(connection); err != nil {
			outcome <- err
			return
		}
		acknowledgment, err := EncodeCommand([]map[string]any{{
			"A": 1, "V": 1, "C": result,
		}})
		if err != nil {
			outcome <- err
			return
		}
		if err := writeFull(connection, acknowledgment); err != nil {
			outcome <- err
			return
		}
		connection.SetReadDeadline(time.Now().Add(time.Second))
		_, err = ReadPacket(connection)
		if err == nil {
			outcome <- errors.New("bridge sent a LIVE request after authentication rejection")
			return
		}
		if !errors.Is(err, ErrConnectionClosed) {
			outcome <- err
			return
		}
		outcome <- nil
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	err = RunSession(ctx, sourceIP.String(), targetIP.String(), rtspURL, 3*time.Second)
	if !errors.Is(err, wantError) {
		t.Fatalf("expected %v, got %v", wantError, err)
	}
	if got := SessionDiagnostic(err); got != diagnostic {
		t.Fatalf("diagnostic = %q, want %q", got, diagnostic)
	}
	if err := <-outcome; err != nil {
		t.Fatal(err)
	}
}
