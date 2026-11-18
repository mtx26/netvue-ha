package netvue

import (
	"bytes"
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
	if !video || frame.timestamp != 0x12345678 || !bytes.Equal(frame.accessUnit, []byte{0x67, 0x01}) {
		t.Fatalf("unexpected frame: video=%v timestamp=%x payload=%x", video, frame.timestamp, frame.accessUnit)
	}
}

func TestParseMediaFrameRejectsTruncationAndUnknownType(t *testing.T) {
	for _, test := range []struct {
		name string
		body []byte
	}{
		{name: "truncated video frame", body: []byte{0x10, 0, 0, 0, 0}},
		{name: "unknown frame type", body: []byte{0x30, 0, 0, 0, 0, 1}},
	} {
		t.Run(test.name, func(t *testing.T) {
			if _, _, err := parseMediaFrame(test.body); !errors.Is(err, ErrMediaFrame) {
				t.Fatalf("expected ErrMediaFrame, got %v", err)
			}
		})
	}
	t.Run("non-video frame", func(t *testing.T) {
		if _, video, err := parseMediaFrame([]byte{0x20, 0, 0, 0, 0, 1}); err != nil || video {
			t.Fatalf("expected non-video frame, got video=%v err=%v", video, err)
		}
	})
}

func FuzzParseMediaFrame(f *testing.F) {
	f.Add([]byte{0x10, 0, 0, 0, 0, 0x67})
	f.Add([]byte{0x20, 0, 0, 0, 0, 1})
	f.Add([]byte{})
	f.Fuzz(func(t *testing.T, body []byte) {
		if len(body) > maxBody {
			return
		}
		frame, video, err := parseMediaFrame(body)
		if err == nil && video && len(frame.accessUnit) == 0 {
			t.Fatal("video frame has an empty access unit")
		}
	})
}
