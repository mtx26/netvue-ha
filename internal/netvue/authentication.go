package netvue

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"time"
)

const (
	loginTimeout    = 4 * time.Second
	maxLoginPackets = 8
	localUserName   = "localUser"
)

var ErrAuthenticationRejected = errors.New("Netvue authentication rejected")

func authenticate(ctx context.Context, conn net.Conn, camera camera, timeout time.Duration) (int64, error) {
	if timeout <= 0 {
		return 0, fmt.Errorf("%w: authentication timeout must be positive", ErrProtocol)
	}
	if err := contextError(ctx); err != nil {
		return 0, err
	}
	packet, err := loginPacket(camera)
	if err != nil {
		return 0, err
	}
	deadline := time.Now().Add(timeout)
	if ctxDeadline, ok := ctx.Deadline(); ok && ctxDeadline.Before(deadline) {
		deadline = ctxDeadline
	}
	if err := conn.SetDeadline(deadline); err != nil {
		return 0, fmt.Errorf("set authentication deadline: %w", err)
	}
	stopCancel := context.AfterFunc(ctx, func() {
		_ = conn.Close()
	})
	defer func() {
		if contextError(ctx) != nil {
			_ = conn.Close()
		}
		stopCancel()
	}()
	if err := contextError(ctx); err != nil {
		return 0, err
	}

	if err := writeFull(conn, packet); err != nil {
		if ctxErr := contextError(ctx); ctxErr != nil {
			return 0, ctxErr
		}
		return 0, fmt.Errorf("send authentication request: %w", err)
	}

	for range maxLoginPackets {
		response, err := ReadPacket(conn)
		if err != nil {
			if ctxErr := contextError(ctx); ctxErr != nil {
				return 0, ctxErr
			}
			return 0, fmt.Errorf("read authentication response: %w", err)
		}
		if response.Kind != packetCommand {
			return 0, withReason("unexpected packet type", fmt.Errorf("%w: unexpected packet during authentication", ErrProtocol))
		}
		units, err := ParseUnits(response.Body)
		if err != nil {
			return 0, err
		}
		for _, unit := range units {
			action, actionOK := unitInt(unit, "A")
			if !actionOK || action != 1 {
				continue
			}
			version, versionOK := unitInt(unit, "V")
			if !versionOK || (version != 1 && version != 2) {
				return 0, withReason("unsupported authentication response version", fmt.Errorf("%w: unsupported authentication response version", ErrProtocol))
			}
			result, resultOK := unitInt(unit, "C")
			if !resultOK {
				return 0, withReason("missing or invalid authentication result", fmt.Errorf("%w: authentication response has no explicit result", ErrProtocol))
			}
			if err := contextError(ctx); err != nil {
				return 0, err
			}
			stopCancel()
			if err := conn.SetDeadline(time.Time{}); err != nil {
				return 0, fmt.Errorf("clear authentication deadline: %w", err)
			}
			return result, nil
		}
	}
	return 0, withReason("authentication response not found", fmt.Errorf("%w: authentication response not found", ErrProtocol))
}

func contextError(ctx context.Context) error {
	if err, _ := contextStatus(ctx); err != nil {
		return err
	}
	// A net.Conn deadline can fire just before its context timer marks Done.
	if deadline, ok := ctx.Deadline(); ok && !time.Now().Before(deadline) {
		return context.DeadlineExceeded
	}
	return nil
}

func contextStatus(ctx context.Context) (error, error) {
	err := ctx.Err()
	if err == nil {
		return nil, nil
	}
	return err, context.Cause(ctx)
}

func loginPacket(camera camera) ([]byte, error) {
	var random [4]byte
	if _, err := rand.Read(random[:]); err != nil {
		return nil, fmt.Errorf("generate authentication nonce: %w", err)
	}
	nonce := int32(binary.LittleEndian.Uint32(random[:]))
	return EncodeCommand([]map[string]any{{
		"Q": 1,
		"V": 1,
		"P": []any{camera.key, nonce, localUserName, camera.host, camera.port},
	}})
}

func writeFull(writer io.Writer, packet []byte) error {
	for len(packet) > 0 {
		n, err := writer.Write(packet)
		if err != nil {
			return err
		}
		if n == 0 {
			return io.ErrShortWrite
		}
		packet = packet[n:]
	}
	return nil
}
