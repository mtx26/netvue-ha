package netvue

import (
	"encoding/binary"
	"errors"
	"testing"
)

func TestParseMediaFrame(t *testing.T) {
	body := []byte{0x10, 0, 0, 0, 0, 0x67, 0x01}
	binary.LittleEndian.PutUint32(body[1:5], 0x12345678)
	frame, video, err := parseMediaFrame(body)
	if err != nil {
		t.Fatal(err)
	}
	if !video || frame.timestamp != 0x12345678 || string(frame.accessUnit) != string([]byte{0x67, 0x01}) {
		t.Fatalf("unexpected frame: video=%v timestamp=%x payload=%x", video, frame.timestamp, frame.accessUnit)
	}
}

func TestParseMediaFrameRejectsTruncationAndUnknownType(t *testing.T) {
	for _, body := range [][]byte{
		{0x10, 0, 0, 0, 0},
		{0x30, 0, 0, 0, 0, 1},
	} {
		if _, _, err := parseMediaFrame(body); !errors.Is(err, ErrMediaFrame) {
			t.Fatalf("expected ErrMediaFrame, got %v", err)
		}
	}
	if _, video, err := parseMediaFrame([]byte{0x20, 0, 0, 0, 0, 1}); err != nil || video {
		t.Fatalf("expected non-video frame, got video=%v err=%v", video, err)
	}
}
