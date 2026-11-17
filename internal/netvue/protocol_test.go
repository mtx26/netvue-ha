package netvue

import (
	"bytes"
	"encoding/binary"
	"errors"
	"io"
	"testing"
)

func TestCommandPacketRoundTrip(t *testing.T) {
	encoded, err := EncodeCommand([]map[string]any{{"Q": 8, "V": 2, "P": []any{}}})
	if err != nil {
		t.Fatal(err)
	}
	packet, err := ParseDatagram(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if packet.Kind != packetCommand || string(packet.Body) != `[{"P":[],"Q":8,"V":2}]` {
		t.Fatalf("unexpected packet: kind=%d body=%s", packet.Kind, packet.Body)
	}
}

func TestParseDatagramRejectsInvalidLengthsAndFlags(t *testing.T) {
	tests := []struct {
		name   string
		packet []byte
	}{
		{name: "short header", packet: make([]byte, headerSize-1)},
		{name: "zero body", packet: makePacket(0, 0, nil)},
		{name: "oversized body", packet: makePacket(maxBody, 0, nil)},
		{name: "mismatched body", packet: makePacket(2, 0, []byte("x"))},
		{name: "flagged", packet: makePacket(1, 1, []byte("x"))},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := ParseDatagram(test.packet); !errors.Is(err, ErrProtocol) {
				t.Fatalf("expected ErrProtocol, got %v", err)
			}
		})
	}
}

func TestReadPacketHandlesFragmentedStream(t *testing.T) {
	wire := makePacket(5, 0, []byte("hello"))
	packet, err := ReadPacket(&oneByteReader{Reader: bytes.NewReader(wire)})
	if err != nil {
		t.Fatal(err)
	}
	if packet.Kind != packetCommand || string(packet.Body) != "hello" {
		t.Fatalf("unexpected packet: %#v", packet)
	}
}

func TestReadPacketRejectsTruncation(t *testing.T) {
	for _, wire := range [][]byte{
		[]byte{1, 2, 3},
		makePacket(4, 0, []byte("ab")),
	} {
		_, err := ReadPacket(bytes.NewReader(wire))
		if !errors.Is(err, ErrConnectionClosed) {
			t.Fatalf("expected ErrConnectionClosed, got %v", err)
		}
	}
}

func TestUnitIntRequiresJSONInteger(t *testing.T) {
	for _, test := range []struct {
		raw      string
		wantOkay bool
	}{
		{raw: `{"C":0}`, wantOkay: true},
		{raw: `{"C":4}`, wantOkay: true},
		{raw: `{"C":0.0}`},
		{raw: `{"C":"0"}`},
		{raw: `{}`},
	} {
		units, err := ParseUnits([]byte("[" + test.raw + "]"))
		if err != nil {
			t.Fatal(err)
		}
		if _, okay := unitInt(units[0], "C"); okay != test.wantOkay {
			t.Fatalf("unitInt() accepted=%v for %s, want %v", okay, test.raw, test.wantOkay)
		}
	}
	if _, err := ParseUnits([]byte(`[null]`)); !errors.Is(err, ErrProtocol) {
		t.Fatalf("expected ErrProtocol for null command unit, got %v", err)
	}
}

func makePacket(length uint32, flag uint16, body []byte) []byte {
	return makePacketKind(length, flag, packetCommand, body)
}

func makePacketKind(length uint32, flag uint16, kind uint16, body []byte) []byte {
	packet := make([]byte, headerSize+len(body))
	binary.LittleEndian.PutUint32(packet[0:4], length)
	binary.LittleEndian.PutUint16(packet[12:14], flag)
	binary.LittleEndian.PutUint16(packet[14:16], kind)
	copy(packet[headerSize:], body)
	return packet
}

type oneByteReader struct {
	io.Reader
}

func (reader *oneByteReader) Read(buffer []byte) (int, error) {
	if len(buffer) > 1 {
		buffer = buffer[:1]
	}
	return reader.Reader.Read(buffer)
}
