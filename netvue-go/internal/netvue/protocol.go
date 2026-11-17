package netvue

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
)

const (
	headerSize = 16
	maxBody    = 1 << 20

	packetCommand = 200
	packetMedia   = 201
)

var (
	ErrProtocol         = errors.New("invalid Netvue protocol packet")
	ErrConnectionClosed = errors.New("Netvue connection closed while reading")
)

type Packet struct {
	Kind uint16
	Body []byte
}

func EncodeCommand(units []map[string]any) ([]byte, error) {
	body, err := json.Marshal(units)
	if err != nil {
		return nil, fmt.Errorf("encode Netvue command: %w", err)
	}
	if len(body) == 0 || len(body) >= maxBody {
		return nil, fmt.Errorf("%w: body length out of range", ErrProtocol)
	}

	packet := make([]byte, headerSize+len(body))
	binary.LittleEndian.PutUint32(packet[0:4], uint32(len(body)))
	binary.LittleEndian.PutUint16(packet[14:16], packetCommand)
	copy(packet[headerSize:], body)
	return packet, nil
}

func ParseDatagram(data []byte) (Packet, error) {
	if len(data) < headerSize {
		return Packet{}, fmt.Errorf("%w: truncated datagram header", ErrProtocol)
	}
	length, kind, err := validateHeader(data[:headerSize])
	if err != nil {
		return Packet{}, err
	}
	if len(data)-headerSize != length {
		return Packet{}, fmt.Errorf("%w: datagram body length mismatch", ErrProtocol)
	}
	return Packet{Kind: kind, Body: data[headerSize:]}, nil
}

func ReadPacket(reader io.Reader) (Packet, error) {
	header := make([]byte, headerSize)
	if _, err := io.ReadFull(reader, header); err != nil {
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return Packet{}, fmt.Errorf("%w: %w", ErrConnectionClosed, err)
		}
		return Packet{}, fmt.Errorf("read Netvue packet header: %w", err)
	}
	length, kind, err := validateHeader(header)
	if err != nil {
		return Packet{}, err
	}
	body := make([]byte, length)
	if _, err := io.ReadFull(reader, body); err != nil {
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return Packet{}, fmt.Errorf("%w: %w", ErrConnectionClosed, err)
		}
		return Packet{}, fmt.Errorf("read Netvue packet body: %w", err)
	}
	return Packet{Kind: kind, Body: body}, nil
}

func ParseUnits(body []byte) ([]map[string]json.RawMessage, error) {
	var units []map[string]json.RawMessage
	if err := json.Unmarshal(body, &units); err != nil {
		return nil, fmt.Errorf("%w: invalid command JSON: %v", ErrProtocol, err)
	}
	if units == nil {
		return nil, fmt.Errorf("%w: command list is not an array", ErrProtocol)
	}
	for _, unit := range units {
		if unit == nil {
			return nil, fmt.Errorf("%w: command list contains a non-object", ErrProtocol)
		}
	}
	return units, nil
}

func unitInt(unit map[string]json.RawMessage, field string) (int64, bool) {
	raw, ok := unit[field]
	if !ok {
		return 0, false
	}
	var value int64
	if err := json.Unmarshal(raw, &value); err != nil {
		return 0, false
	}
	return value, true
}

func validateHeader(header []byte) (int, uint16, error) {
	if len(header) != headerSize {
		return 0, 0, fmt.Errorf("%w: invalid header size", ErrProtocol)
	}
	length := binary.LittleEndian.Uint32(header[0:4])
	flag := binary.LittleEndian.Uint16(header[12:14])
	kind := binary.LittleEndian.Uint16(header[14:16])
	if length == 0 || length >= maxBody {
		return 0, 0, fmt.Errorf("%w: body length out of range", ErrProtocol)
	}
	if flag != 0 {
		return 0, 0, fmt.Errorf("%w: flagged packets are unsupported", ErrProtocol)
	}
	return int(length), kind, nil
}
