package netvue

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net"
	"reflect"
	"testing"
	"time"
)

func TestDiscoverLANFiltersAndDeduplicates(t *testing.T) {
	server, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.2")})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	outside, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.4")})
	if err != nil {
		t.Fatal(err)
	}
	defer outside.Close()
	response := discoveryResponse(t, "127.0.0.2", 5555, "fake-key", 0)
	wrongHost := discoveryResponse(t, "127.0.0.3", 5555, "fake-key", 0)
	outsideResponse := discoveryResponse(t, "127.0.0.4", 5555, "fake-key", 0)
	request, err := EncodeCommand([]map[string]any{{"Q": 8, "V": 2, "P": []any{}}})
	if err != nil {
		t.Fatal(err)
	}
	serverErr := make(chan error, 1)
	go func() {
		buf := make([]byte, 1024)
		_ = server.SetReadDeadline(time.Now().Add(5 * time.Second))
		n, peer, err := server.ReadFromUDP(buf)
		if err != nil {
			serverErr <- err
			return
		}
		if !bytes.Equal(buf[:n], request) {
			serverErr <- errors.New("invalid discovery request")
			return
		}
		if _, err := outside.WriteToUDP(outsideResponse, peer); err != nil {
			serverErr <- err
			return
		}
		for _, data := range [][]byte{[]byte("invalid"), wrongHost, response, response} {
			if _, err := server.WriteToUDP(data, peer); err != nil {
				serverErr <- err
				return
			}
		}
		serverErr <- nil
	}()
	_, subnet, _ := net.ParseCIDR("127.0.0.0/30")
	hosts, err := discoverLAN(context.Background(), net.ParseIP("127.0.0.1"), subnet, server.LocalAddr().(*net.UDPAddr))
	if err != nil {
		t.Fatal(err)
	}
	if err := <-serverErr; err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(hosts, []string{"127.0.0.2"}) {
		t.Fatalf("discovered hosts = %v", hosts)
	}
}

func TestDiscoverLANEmptyAndCancellation(t *testing.T) {
	server, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.2")})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	_, subnet, _ := net.ParseCIDR("127.0.0.0/8")
	hosts, err := discoverLAN(context.Background(), net.ParseIP("127.0.0.1"), subnet, server.LocalAddr().(*net.UDPAddr))
	if err != nil || len(hosts) != 0 || hosts == nil {
		t.Fatalf("empty discovery = %v, %v", hosts, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := discoverLAN(ctx, net.ParseIP("127.0.0.1"), subnet, server.LocalAddr().(*net.UDPAddr)); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled discovery error = %v", err)
	}
}

func TestParseDiscoveryValidatesSourceAndEndpoint(t *testing.T) {
	packet, err := EncodeCommand([]map[string]any{{
		"A": 8,
		"V": 2,
		"C": 0,
		"P": []any{"fake-id", "fake-fw", "192.168.10.2", 5555, "fake-key", []bool{true, false, false, false}},
	}})
	if err != nil {
		t.Fatal(err)
	}
	result, err := parseDiscovery(packet, "192.168.10.2", "192.168.10.2")
	if err != nil {
		t.Fatal(err)
	}
	if result.host != "192.168.10.2" || result.port != 5555 {
		t.Fatalf("unexpected camera: %s", result)
	}
	if !result.audio {
		t.Fatal("audio capability was not parsed from discovery")
	}
	if fmt.Sprint(result) != "camera{192.168.10.2:5555}" {
		t.Fatalf("camera string exposes unexpected information: %s", result)
	}
	for _, test := range []struct {
		name string
		data []byte
		src  string
	}{
		{name: "unexpected source", data: packet, src: "192.168.10.3"},
		{name: "announced host mismatch", data: discoveryResponse(t, "192.168.10.3", 5555, "fake-key", 0), src: "192.168.10.2"},
		{name: "empty key", data: discoveryResponse(t, "192.168.10.2", 5555, "", 0), src: "192.168.10.2"},
		{name: "failure result", data: discoveryResponse(t, "192.168.10.2", 5555, "fake-key", 1), src: "192.168.10.2"},
	} {
		t.Run(test.name, func(t *testing.T) {
			if _, err := parseDiscovery(test.data, test.src, "192.168.10.2"); !errors.Is(err, ErrProtocol) {
				t.Fatalf("expected ErrProtocol, got %v", err)
			}
		})
	}
}

func FuzzParseDiscovery(f *testing.F) {
	valid, err := EncodeCommand([]map[string]any{{
		"A": 8, "V": 2, "C": 0,
		"P": []any{"fake-id", "fake-fw", "192.168.10.2", 5555, "fake-key"},
	}})
	if err != nil {
		f.Fatal(err)
	}
	f.Add(valid)
	f.Add([]byte{})
	f.Fuzz(func(t *testing.T, data []byte) {
		if len(data) > 65535 {
			return
		}
		_, _ = parseDiscovery(data, "192.168.10.2", "192.168.10.2")
	})
}

func TestValidateLANIPv4RejectsPublicAndNonIPv4(t *testing.T) {
	for _, test := range []struct {
		address string
		valid   bool
	}{
		{address: "203.0.113.10"},
		{address: "2001:db8::1"},
		{address: "not-an-address"},
		{address: "192.168.1.12", valid: true},
		{address: "10.1.2.3", valid: true},
		{address: "127.0.0.1", valid: true},
	} {
		_, err := validateLANIPv4(test.address)
		if gotValid := err == nil; gotValid != test.valid {
			t.Fatalf("validateLANIPv4(%q) valid=%v, want %v (err=%v)",
				test.address, gotValid, test.valid, err)
		}
	}
}

func TestParseDiscoveryRejectsNullResult(t *testing.T) {
	packet, err := EncodeCommand([]map[string]any{{
		"A": 8, "V": 2, "C": nil,
		"P": []any{"fake-id", "fake-fw", "192.168.10.2", 5555, "fake-key"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := parseDiscovery(packet, "192.168.10.2", "192.168.10.2"); !errors.Is(err, ErrProtocol) {
		t.Fatalf("expected ErrProtocol for null result, got %v", err)
	}
}

func discoveryResponse(t *testing.T, host string, port int, key string, code int) []byte {
	t.Helper()
	packet, err := EncodeCommand([]map[string]any{{
		"A": 8, "V": 2, "C": code,
		"P": []any{"fake-id", "fake-fw", host, port, key},
	}})
	if err != nil {
		t.Fatal(err)
	}
	return packet
}
