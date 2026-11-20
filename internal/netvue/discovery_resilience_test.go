package netvue

import (
	"context"
	"errors"
	"net"
	"testing"
	"time"
)

func startDiscoveryResponder(t *testing.T, responses ...[]byte) <-chan error {
	t.Helper()

	listener, err := net.ListenUDP("udp4", &net.UDPAddr{
		IP:   net.IPv4(127, 0, 0, 2),
		Port: discoveryPort,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = listener.Close()
	})

	done := make(chan error, 1)
	go func() {
		if err := listener.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
			done <- err
			return
		}
		buffer := make([]byte, 1024)
		_, remote, err := listener.ReadFromUDP(buffer)
		if err != nil {
			done <- err
			return
		}
		for _, response := range responses {
			if _, err := listener.WriteToUDP(response, remote); err != nil {
				done <- err
				return
			}
		}
		done <- nil
	}()
	return done
}

func TestDiscoverIgnoresMalformedReplyBeforeValidReply(t *testing.T) {
	validReply := discoveryResponse(t, "127.0.0.2", 9000, "test", 0)
	responderDone := startDiscoveryResponder(t, []byte("malformed"), validReply)

	device, err := discover(context.Background(), "127.0.0.1", "127.0.0.2")
	if err != nil {
		t.Fatalf("discover() failed after a valid reply: %v", err)
	}
	if device.host != "127.0.0.2" || device.port != 9000 {
		t.Fatalf("device endpoint = %s:%d, want 127.0.0.2:9000", device.host, device.port)
	}
	if err := <-responderDone; err != nil {
		t.Fatalf("responder failed: %v", err)
	}
}

func TestDiscoverLANResendsLostRequest(t *testing.T) {
	listener, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 2)})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	reply := discoveryResponse(t, "127.0.0.2", 5555, "test", 0)
	done := make(chan error, 1)
	go func() {
		if err := listener.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
			done <- err
			return
		}
		buffer := make([]byte, 1024)
		if _, _, err := listener.ReadFromUDP(buffer); err != nil {
			done <- err
			return
		}
		_, remote, err := listener.ReadFromUDP(buffer)
		if err == nil {
			_, err = listener.WriteToUDP(reply, remote)
		}
		done <- err
	}()
	_, subnet, err := net.ParseCIDR("127.0.0.0/30")
	if err != nil {
		t.Fatal(err)
	}

	hosts, err := discoverLAN(context.Background(), net.IPv4(127, 0, 0, 1), subnet, listener.LocalAddr().(*net.UDPAddr))
	if err != nil {
		t.Fatal(err)
	}
	if err := <-done; err != nil {
		t.Fatalf("responder failed: %v", err)
	}
	if len(hosts) != 1 || hosts[0] != "127.0.0.2" {
		t.Fatalf("discovered hosts = %v, want camera answering the resent request", hosts)
	}
}

func TestDiscoverTimesOutAfterOnlyMalformedReply(t *testing.T) {
	responderDone := startDiscoveryResponder(t, []byte("malformed"))

	_, err := discover(context.Background(), "127.0.0.1", "127.0.0.2")
	if !errors.Is(err, ErrDiscoveryTimeout) {
		t.Fatalf("discover() error = %v, want ErrDiscoveryTimeout", err)
	}
	if err := <-responderDone; err != nil {
		t.Fatalf("responder failed: %v", err)
	}
}

func TestDiscoverReturnsExplicitRejection(t *testing.T) {
	rejectedReply := discoveryResponse(t, "127.0.0.2", 9000, "test", 1)
	validReply := discoveryResponse(t, "127.0.0.2", 9000, "test", 0)
	responderDone := startDiscoveryResponder(t, rejectedReply, validReply)

	_, err := discover(context.Background(), "127.0.0.1", "127.0.0.2")
	if !errors.Is(err, ErrProtocol) || !errors.Is(err, errDiscoveryRejected) {
		t.Fatalf("discover() error = %v, want explicit protocol rejection", err)
	}
	if err := <-responderDone; err != nil {
		t.Fatalf("responder failed: %v", err)
	}
}

func TestDiscoverIgnoresMalformedResultCode(t *testing.T) {
	malformedReply, err := EncodeCommand([]map[string]any{{
		"A": 8, "V": 2, "C": "1",
		"P": []any{"fake-id", "fake-fw", "192.168.10.2", 5555, "fake-key"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	validReply := discoveryResponse(t, "127.0.0.2", 9000, "test", 0)
	responderDone := startDiscoveryResponder(t, malformedReply, validReply)

	device, err := discover(context.Background(), "127.0.0.1", "127.0.0.2")
	if err != nil {
		t.Fatalf("discover() failed after a valid reply: %v", err)
	}
	if device.host != "127.0.0.2" || device.port != 9000 {
		t.Fatalf("device endpoint = %s:%d, want 127.0.0.2:9000", device.host, device.port)
	}
	if err := <-responderDone; err != nil {
		t.Fatalf("responder failed: %v", err)
	}
}
