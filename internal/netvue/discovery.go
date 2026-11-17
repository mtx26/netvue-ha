package netvue

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"sort"
	"strconv"
	"time"
)

var errDiscoveryRejected = errors.New("camera rejected discovery response")

const (
	discoveryPort        = 36868
	discoveryTimeout     = 2 * time.Second
	maxDiscoveredCameras = 256
)

var ErrDiscoveryTimeout = errors.New("Netvue discovery timed out")

// DiscoverLAN sends one discovery request to the source interface's subnet.
// Only hosts compatible with the existing unicast session protocol are returned.
func DiscoverLAN(ctx context.Context, source string) ([]string, error) {
	sourceIP, err := validateLANIPv4(source)
	if err != nil {
		return nil, fmt.Errorf("invalid source address: %w", err)
	}
	interfaces, err := net.Interfaces()
	if err != nil {
		return nil, fmt.Errorf("list network interfaces: %w", err)
	}
	for _, iface := range interfaces {
		addrs, err := iface.Addrs()
		if err != nil {
			return nil, fmt.Errorf("list interface addresses: %w", err)
		}
		for _, addr := range addrs {
			ip, subnet, err := net.ParseCIDR(addr.String())
			if err != nil || ip.To4() == nil || !ip.Equal(sourceIP) {
				continue
			}
			ones, bits := subnet.Mask.Size()
			if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagBroadcast == 0 || bits != 32 || ones > 30 {
				return nil, errors.New("source interface does not support IPv4 broadcast")
			}
			broadcast := make(net.IP, net.IPv4len)
			for i := range broadcast {
				broadcast[i] = subnet.IP.To4()[i] | ^subnet.Mask[i]
			}
			return discoverLAN(ctx, sourceIP, subnet, &net.UDPAddr{IP: broadcast, Port: discoveryPort})
		}
	}
	return nil, errors.New("source address is not assigned to a local interface")
}

func openDiscoverySocket(ctx context.Context, source net.IP, target *net.UDPAddr) (*net.UDPConn, func() bool, error) {
	request, err := EncodeCommand([]map[string]any{{"Q": 8, "V": 2, "P": []any{}}})
	if err != nil {
		return nil, nil, err
	}
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: source})
	if err != nil {
		return nil, nil, fmt.Errorf("bind discovery socket: %w", err)
	}
	closeOnError := func(err error) (*net.UDPConn, func() bool, error) {
		_ = conn.Close()
		return nil, nil, err
	}
	deadline := time.Now().Add(discoveryTimeout)
	if ctxDeadline, ok := ctx.Deadline(); ok && ctxDeadline.Before(deadline) {
		deadline = ctxDeadline
	}
	if err := conn.SetDeadline(deadline); err != nil {
		return closeOnError(fmt.Errorf("set discovery deadline: %w", err))
	}
	stopCancel := context.AfterFunc(ctx, func() { _ = conn.SetDeadline(time.Now()) })
	if err := contextError(ctx); err != nil {
		stopCancel()
		return closeOnError(err)
	}
	if _, err := conn.WriteToUDP(request, target); err != nil {
		stopCancel()
		return closeOnError(fmt.Errorf("send discovery request: %w", err))
	}
	return conn, stopCancel, nil
}

func discoverLAN(ctx context.Context, source net.IP, subnet *net.IPNet, target *net.UDPAddr) ([]string, error) {
	conn, stopCancel, err := openDiscoverySocket(ctx, source, target)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	defer stopCancel()
	found := make(map[string]struct{})
	buffer := make([]byte, 65535)
	for {
		n, remote, err := conn.ReadFromUDP(buffer)
		if err != nil {
			if ctxErr := contextError(ctx); ctxErr != nil {
				return nil, ctxErr
			}
			if timeout, ok := err.(net.Error); ok && timeout.Timeout() {
				hosts := make([]string, 0, len(found))
				for host := range found {
					hosts = append(hosts, host)
				}
				sort.Strings(hosts)
				return hosts, nil
			}
			return nil, fmt.Errorf("receive discovery response: %w", err)
		}
		if remote == nil || remote.IP.Equal(source) || !subnet.Contains(remote.IP) {
			continue
		}
		host := remote.IP.String()
		if _, err := parseDiscovery(buffer[:n], host, host); err != nil {
			continue
		}
		if _, exists := found[host]; !exists && len(found) == maxDiscoveredCameras {
			return nil, errors.New("too many discovery responses")
		}
		found[host] = struct{}{}
	}
}

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

	conn, stopCancel, err := openDiscoverySocket(
		ctx, sourceIP, &net.UDPAddr{IP: targetIP, Port: discoveryPort},
	)
	if err != nil {
		return camera{}, err
	}
	defer conn.Close()
	defer stopCancel()

	buffer := make([]byte, 65535)
	var malformed error
	for {
		n, remote, err := conn.ReadFromUDP(buffer)
		if err != nil {
			if ctxErr := contextError(ctx); ctxErr != nil {
				return camera{}, ctxErr
			}
			if timeout, ok := err.(net.Error); ok && timeout.Timeout() {
				if malformed != nil {
					return camera{}, fmt.Errorf("%w: malformed response: %v", ErrDiscoveryTimeout, malformed)
				}
				return camera{}, ErrDiscoveryTimeout
			}
			return camera{}, fmt.Errorf("receive discovery response: %w", err)
		}
		if remote == nil || !remote.IP.Equal(targetIP) {
			continue
		}
		device, parseErr := parseDiscovery(buffer[:n], remote.IP.String(), targetIP.String())
		if parseErr == nil {
			return device, nil
		}
		if errors.Is(parseErr, errDiscoveryRejected) {
			return camera{}, parseErr
		}
		malformed = parseErr
	}
}

func parseDiscovery(data []byte, source, expected string) (camera, error) {
	expectedIP, err := validateLANIPv4(expected)
	if err != nil {
		return camera{}, err
	}
	sourceIP := net.ParseIP(source)
	if sourceIP == nil || !sourceIP.Equal(expectedIP) {
		return camera{}, withReason("response from unexpected host", fmt.Errorf("%w: discovery response from unexpected host", ErrProtocol))
	}
	packet, err := ParseDatagram(data)
	if err != nil {
		return camera{}, err
	}
	if packet.Kind != packetCommand {
		return camera{}, withReason("unexpected packet type", fmt.Errorf("%w: unexpected discovery packet type", ErrProtocol))
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
			if !ok {
				return camera{}, withReason("invalid discovery result code", fmt.Errorf("%w: invalid discovery result code", ErrProtocol))
			}
			if code != 0 {
				return camera{}, withReason("discovery response reported failure", fmt.Errorf("%w: %w", ErrProtocol, errDiscoveryRejected))
			}
		}
		rawParams, ok := unit["P"]
		if !ok {
			return camera{}, withReason("discovery parameters are missing", fmt.Errorf("%w: discovery parameters are missing", ErrProtocol))
		}
		var params []json.RawMessage
		if err := json.Unmarshal(rawParams, &params); err != nil || len(params) < 5 {
			return camera{}, withReason("invalid discovery parameters", fmt.Errorf("%w: invalid discovery parameters", ErrProtocol))
		}
		var host, key string
		var port int
		if json.Unmarshal(params[2], &host) != nil ||
			json.Unmarshal(params[3], &port) != nil ||
			json.Unmarshal(params[4], &key) != nil {
			return camera{}, withReason("invalid discovery endpoint", fmt.Errorf("%w: invalid discovery endpoint", ErrProtocol))
		}
		hostIP := net.ParseIP(host)
		if hostIP == nil || !hostIP.Equal(expectedIP) || port < 1 || port > 65535 || key == "" {
			return camera{}, withReason("invalid or mismatched discovery endpoint", fmt.Errorf("%w: discovery endpoint does not match target", ErrProtocol))
		}
		return camera{host: expectedIP.String(), port: port, key: key}, nil
	}
	return camera{}, withReason("no supported discovery response", fmt.Errorf("%w: no supported discovery response", ErrProtocol))
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

func ValidateLANIPv4(value string) error {
	_, err := validateLANIPv4(value)
	return err
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
