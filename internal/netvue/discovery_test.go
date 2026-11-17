package netvue

import (
	"errors"
	"fmt"
	"testing"
)

func TestParseDiscoveryValidatesSourceAndEndpoint(t *testing.T) {
	packet, err := EncodeCommand([]map[string]any{{
		"A": 8,
		"V": 2,
		"C": 0,
		"P": []any{"fake-id", "fake-fw", "192.168.10.2", 5555, "fake-key"},
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
