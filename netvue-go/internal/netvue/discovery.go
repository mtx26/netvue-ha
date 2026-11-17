package netvue

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"strconv"
	"time"
)

const (
	discoveryPort    = 36868
	discoveryTimeout = 2 * time.Second
)

var ErrDiscoveryTimeout = errors.New("Netvue discovery timed out")

type camera struct {
	host string
	port int
	key  string
}

func discover(ctx context.Context, source, target string) (camera, error) {
	sourceIP, err := validateLANIPv4(source)
	if err != nil {
		return camera{}, fmt.Errorf("invalid source address: %w", err)
	}
	targetIP, err := validateLANIPv4(target)
	if err != nil {
		return camera{}, fmt.Errorf("invalid camera address: %w", err)
	}
	if sourceIP.Equal(targetIP) {
		return camera{}, fmt.Errorf("%w: source and camera addresses must differ", ErrProtocol)
	}

	request, err := EncodeCommand([]map[string]any{{"Q": 8, "V": 2, "P": []any{}}})
	if err != nil {
		return camera{}, err
	}
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: sourceIP})
	if err != nil {
		return camera{}, fmt.Errorf("bind discovery socket: %w", err)
	}
	defer conn.Close()
	deadline := time.Now().Add(discoveryTimeout)
	if ctxDeadline, ok := ctx.Deadline(); ok && ctxDeadline.Before(deadline) {
		deadline = ctxDeadline
	}
	if err := conn.SetDeadline(deadline); err != nil {
		return camera{}, fmt.Errorf("set discovery deadline: %w", err)
	}
	stopCancel := context.AfterFunc(ctx, func() {
		_ = conn.SetDeadline(time.Now())
	})
	defer stopCancel()
	if err := contextError(ctx); err != nil {
		return camera{}, err
	}
	if _, err := conn.WriteToUDP(request, &net.UDPAddr{IP: targetIP, Port: discoveryPort}); err != nil {
		return camera{}, fmt.Errorf("send discovery request: %w", err)
	}

	buffer := make([]byte, 65535)
	for {
		n, remote, err := conn.ReadFromUDP(buffer)
		if err != nil {
			if ctxErr := contextError(ctx); ctxErr != nil {
				return camera{}, ctxErr
			}
			if timeout, ok := err.(net.Error); ok && timeout.Timeout() {
				return camera{}, ErrDiscoveryTimeout
			}
			return camera{}, fmt.Errorf("receive discovery response: %w", err)
		}
		if remote == nil || !remote.IP.Equal(targetIP) {
			continue
		}
		return parseDiscovery(buffer[:n], remote.IP.String(), targetIP.String())
	}
}

func parseDiscovery(data []byte, source, expected string) (camera, error) {
	expectedIP, err := validateLANIPv4(expected)
	if err != nil {
		return camera{}, err
	}
	sourceIP := net.ParseIP(source)
	if sourceIP == nil || !sourceIP.Equal(expectedIP) {
		return camera{}, fmt.Errorf("%w: discovery response from unexpected host", ErrProtocol)
	}
	packet, err := ParseDatagram(data)
	if err != nil {
		return camera{}, err
	}
	if packet.Kind != packetCommand {
		return camera{}, fmt.Errorf("%w: unexpected discovery packet type", ErrProtocol)
	}
	units, err := ParseUnits(packet.Body)
	if err != nil {
		return camera{}, err
	}
	for _, unit := range units {
		action, actionOK := unitInt(unit, "A")
		version, versionOK := unitInt(unit, "V")
		if !actionOK || !versionOK || action != 8 || version != 2 {
			continue
		}
		if _, hasCode := unit["C"]; hasCode {
			code, ok := unitInt(unit, "C")
			if !ok || code != 0 {
				return camera{}, fmt.Errorf("%w: discovery response reported failure", ErrProtocol)
			}
		}
		rawParams, ok := unit["P"]
		if !ok {
			return camera{}, fmt.Errorf("%w: discovery parameters are missing", ErrProtocol)
		}
		var params []json.RawMessage
		if err := json.Unmarshal(rawParams, &params); err != nil || len(params) < 5 {
			return camera{}, fmt.Errorf("%w: invalid discovery parameters", ErrProtocol)
		}
		var host, key string
		var port int
		if json.Unmarshal(params[2], &host) != nil ||
			json.Unmarshal(params[3], &port) != nil ||
			json.Unmarshal(params[4], &key) != nil {
			return camera{}, fmt.Errorf("%w: invalid discovery endpoint", ErrProtocol)
		}
		hostIP := net.ParseIP(host)
		if hostIP == nil || !hostIP.Equal(expectedIP) || port < 1 || port > 65535 || key == "" {
			return camera{}, fmt.Errorf("%w: discovery endpoint does not match target", ErrProtocol)
		}
		return camera{host: expectedIP.String(), port: port, key: key}, nil
	}
	return camera{}, fmt.Errorf("%w: no supported discovery response", ErrProtocol)
}

func validateLANIPv4(value string) (net.IP, error) {
	ip := net.ParseIP(value)
	if ip == nil || ip.To4() == nil {
		return nil, fmt.Errorf("%q is not an IPv4 address", value)
	}
	ip = ip.To4()
	if !ip.IsLoopback() && !ip.IsPrivate() {
		return nil, fmt.Errorf("%q is not a private or loopback IPv4 address", value)
	}
	return ip, nil
}

func (c camera) endpoint() string {
	return net.JoinHostPort(c.host, strconv.Itoa(c.port))
}

func (c camera) String() string {
	return fmt.Sprintf("camera{%s}", c.endpoint())
}

func (c camera) GoString() string {
	return c.String()
}
