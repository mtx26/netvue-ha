package netvue

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"slices"
	"testing"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue/netvuetest"
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
		if err := server.SetReadDeadline(time.Now().Add(5 * time.Second)); err != nil {
			serverErr <- err
			return
		}
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
	_, subnet, err := net.ParseCIDR("127.0.0.0/30")
	if err != nil {
		t.Fatal(err)
	}
	hosts, err := discoverLAN(context.Background(), net.ParseIP("127.0.0.1"), subnet, server.LocalAddr().(*net.UDPAddr))
	if err != nil {
		t.Fatal(err)
	}
	if err := <-serverErr; err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(hosts, []string{"127.0.0.2"}) {
		t.Fatalf("discovered hosts = %v", hosts)
	}
}

func TestDiscoverLANEmptyAndCancellation(t *testing.T) {
	server, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.2")})
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	_, subnet, err := net.ParseCIDR("127.0.0.0/8")
	if err != nil {
		t.Fatal(err)
	}
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
		"P": []any{
			"fake-id", "fake-fw", "192.168.10.2", 5555, "fake-key",
			[]any{true, false, true, false, "local-wifi", -47},
			map[string]any{"unverified": true}, 27, "offline-token",
		},
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
	if result.metadata == nil ||
		result.metadata.SerialNumber != "fake-id" ||
		result.metadata.FirmwareVersion != "fake-fw" ||
		result.metadata.WiFiSSID != "local-wifi" ||
		result.metadata.WiFiSignalLevel == nil ||
		*result.metadata.WiFiSignalLevel != -47 {
		t.Fatalf("unexpected discovery metadata: %+v", result.metadata)
	}
	if capabilities := result.metadata.Capabilities; capabilities == nil ||
		capabilities.Audio == nil || !*capabilities.Audio ||
		capabilities.Flip == nil || *capabilities.Flip ||
		capabilities.Light == nil || !*capabilities.Light ||
		capabilities.SDCard == nil || *capabilities.SDCard {
		t.Fatalf("unexpected discovery capabilities: %+v", result.metadata.Capabilities)
	}
	serialized, err := json.Marshal(result.discoveryResult())
	if err != nil {
		t.Fatal(err)
	}
	for _, sensitive := range []string{"fake-key", "offline-token", "unverified"} {
		if bytes.Contains(serialized, []byte(sensitive)) {
			t.Fatalf("discovery API result exposed %q: %s", sensitive, serialized)
		}
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

func TestParseDiscoveryAcceptsOlderAndIncompleteOptionalFields(t *testing.T) {
	tests := []struct {
		name         string
		params       []any
		wantAudio    bool
		wantMetadata bool
	}{
		{
			name:         "legacy response without extended fields",
			params:       []any{"fake-id", "fake-fw", "192.168.10.2", 5555, "fake-key"},
			wantMetadata: true,
		},
		{
			name: "partial capabilities",
			params: []any{
				"fake-id", "fake-fw", "192.168.10.2", 5555, "fake-key",
				[]any{true},
			},
			wantAudio:    true,
			wantMetadata: true,
		},
		{
			name: "malformed optional values are ignored",
			params: []any{
				"bad\nserial", 12, "192.168.10.2", 5555, "fake-key",
				[]any{nil, "true", 1, false, "\n", "not-an-int"},
			},
			wantMetadata: true,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			packet, err := EncodeCommand([]map[string]any{{"A": 8, "V": 2, "P": test.params}})
			if err != nil {
				t.Fatal(err)
			}
			result, err := parseDiscovery(packet, "192.168.10.2", "192.168.10.2")
			if err != nil {
				t.Fatalf("parseDiscovery() error = %v", err)
			}
			if result.audio != test.wantAudio {
				t.Errorf("audio = %v, want %v", result.audio, test.wantAudio)
			}
			if gotMetadata := result.metadata != nil; gotMetadata != test.wantMetadata {
				t.Fatalf("metadata present = %v, want %v (%+v)", gotMetadata, test.wantMetadata, result.metadata)
			}
			if test.name == "malformed optional values are ignored" {
				if result.metadata.SerialNumber != "" || result.metadata.FirmwareVersion != "" ||
					result.metadata.WiFiSSID != "" || result.metadata.WiFiSignalLevel != nil {
					t.Fatalf("malformed optional values were retained: %+v", result.metadata)
				}
				if result.metadata.Capabilities == nil ||
					result.metadata.Capabilities.SDCard == nil ||
					*result.metadata.Capabilities.SDCard {
					t.Fatalf("valid SD-card capability was discarded: %+v", result.metadata.Capabilities)
				}
			}
		})
	}
}

func TestDiscoverCameraMetadataFromSimulator(t *testing.T) {
	signal := -37
	camera := netvuetest.Start(t, netvuetest.Options{
		Host:            netvuetest.AllocHost(44),
		SerialNumber:    "sim-serial",
		FirmwareVersion: "sim-fw-2",
		Audio:           true,
		Flip:            true,
		Light:           false,
		SDCard:          true,
		WiFiSSID:        "synthetic-wifi",
		WiFiSignalLevel: &signal,
	})
	result, err := DiscoverCameraMetadata(context.Background(), "127.0.0.1", camera.Host())
	if err != nil {
		t.Fatalf("DiscoverCameraMetadata() error = %v", err)
	}
	if result.Host != camera.Host() || result.ConnectionPort != camera.Port() ||
		result.Metadata == nil ||
		result.Metadata.SerialNumber != "sim-serial" ||
		result.Metadata.FirmwareVersion != "sim-fw-2" ||
		result.Metadata.WiFiSSID != "synthetic-wifi" ||
		result.Metadata.WiFiSignalLevel == nil ||
		*result.Metadata.WiFiSignalLevel != signal {
		t.Fatalf("discovery result = %+v", result)
	}
	if capabilities := result.Metadata.Capabilities; capabilities == nil ||
		capabilities.Audio == nil || !*capabilities.Audio ||
		capabilities.Flip == nil || !*capabilities.Flip ||
		capabilities.Light == nil || *capabilities.Light ||
		capabilities.SDCard == nil || !*capabilities.SDCard {
		t.Fatalf("discovery capabilities = %+v", result.Metadata.Capabilities)
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
		name    string
		address string
		valid   bool
	}{
		{name: "public IPv4", address: "203.0.113.10"},
		{name: "IPv6", address: "2001:db8::1"},
		{name: "invalid text", address: "not-an-address"},
		{name: "private class C", address: "192.168.1.12", valid: true},
		{name: "private class A", address: "10.1.2.3", valid: true},
		{name: "loopback", address: "127.0.0.1", valid: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			_, err := validateLANIPv4(test.address)
			if gotValid := err == nil; gotValid != test.valid {
				t.Fatalf("validateLANIPv4(%q) valid=%v, want %v (err=%v)",
					test.address, gotValid, test.valid, err)
			}
		})
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
