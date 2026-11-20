package manager

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"testing"
	"time"

	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/mpeg4audio"
	mediopus "github.com/bluenviron/mediacommon/v2/pkg/codecs/opus"
	"github.com/pion/opus"
	"github.com/pion/rtp"
)

func TestAACAccessUnitToADTS(t *testing.T) {
	audio := &format.MPEG4Audio{Config: &mpeg4audio.AudioSpecificConfig{
		Type: mpeg4audio.ObjectTypeAACLC, SampleRate: 8000, ChannelConfig: 1,
	}}
	frame, err := aacAccessUnitToADTS(audio, []byte{1, 2, 3})
	if err != nil {
		t.Fatal(err)
	}
	if len(frame) != 10 {
		t.Fatalf("ADTS frame length = %d, want 10: %x", len(frame), frame)
	}
	if frame[0] != 0xff || frame[1] != 0xf1 ||
		(frame[2]>>6) != 1 || (frame[2]>>2)&15 != 11 ||
		((frame[2]&1)<<2|frame[3]>>6) != 1 {
		t.Fatalf("invalid AAC-LC ADTS header: %x", frame[:7])
	}
	frameLength := int(frame[3]&3)<<11 | int(frame[4])<<3 | int(frame[5]>>5)
	if frameLength != len(frame) {
		t.Fatalf("ADTS frame length = %d, want %d", frameLength, len(frame))
	}
}

func TestAACAccessUnitToADTSRejectsUnsupportedFormats(t *testing.T) {
	for _, test := range []struct {
		name  string
		audio *format.MPEG4Audio
	}{
		{"missing format", nil},
		{"unsupported object type", &format.MPEG4Audio{Config: &mpeg4audio.AudioSpecificConfig{Type: mpeg4audio.ObjectType(1), SampleRate: 8000, ChannelConfig: 1}}},
		{"missing channel configuration", &format.MPEG4Audio{Config: &mpeg4audio.AudioSpecificConfig{Type: mpeg4audio.ObjectTypeAACLC, SampleRate: 8000}}},
		{"unsupported sample rate", &format.MPEG4Audio{Config: &mpeg4audio.AudioSpecificConfig{Type: mpeg4audio.ObjectTypeAACLC, SampleRate: 12345, ChannelConfig: 1}}},
	} {
		t.Run(test.name, func(t *testing.T) {
			if _, err := aacAccessUnitToADTS(test.audio, []byte{1}); err == nil {
				t.Fatalf("accepted unsupported audio format: %+v", test.audio)
			}
		})
	}
	if _, err := aacAccessUnitToADTS(&format.MPEG4Audio{Config: &mpeg4audio.AudioSpecificConfig{
		Type: mpeg4audio.ObjectTypeAACLC, SampleRate: 8000, ChannelConfig: 1,
	}}, make([]byte, 8192)); err == nil {
		t.Fatal("accepted oversized AAC access unit")
	}
}

type capturedRTPTrack struct {
	packets chan *rtp.Packet
}

func (track *capturedRTPTrack) WriteRTP(packet *rtp.Packet) error {
	copyPacket := *packet
	copyPacket.Payload = append([]byte(nil), packet.Payload...)
	select {
	case track.packets <- &copyPacket:
		return nil
	default:
		return errors.New("RTP capture buffer full")
	}
}

func TestAACToOpusRTPWithFFmpeg(t *testing.T) {
	ffmpeg := os.Getenv("NETVUE_TEST_FFMPEG_FULL")
	if ffmpeg == "" {
		t.Skip("set NETVUE_TEST_FFMPEG_FULL to test AAC-to-Opus conversion")
	}
	frames := syntheticAACAccessUnits(t, ffmpeg, 1)
	source := make(chan aacRTPFrame, len(frames))
	const firstTimestamp = 123456
	audioFormat := &format.MPEG4Audio{Config: &mpeg4audio.AudioSpecificConfig{
		Type: mpeg4audio.ObjectTypeAACLC, SampleRate: 8000, ChannelConfig: 1,
	}}
	for i, frame := range frames {
		adts, err := aacAccessUnitToADTS(audioFormat, frame)
		if err != nil {
			t.Fatal(err)
		}
		source <- aacRTPFrame{
			data: adts, timestamp: firstTimestamp + uint32(i*1024*48000/8000),
		}
	}
	close(source)
	track := &capturedRTPTrack{packets: make(chan *rtp.Packet, 128)}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- transcodeAACToOpusRTP(ctx, ffmpeg, source, track) }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatalf("AAC-to-Opus conversion did not stop before deadline: %v", ctx.Err())
	}
	close(track.packets)
	decoder, err := opus.NewDecoderWithOutput(48000, 2)
	if err != nil {
		t.Fatal(err)
	}
	samples := make([]int16, 5760*2)
	packetCount, audible := 0, false
	var lastTimestamp, lastDuration uint32
	for packet := range track.packets {
		if packet.PayloadType != 111 || len(packet.Payload) == 0 {
			t.Fatalf("invalid Opus RTP packet: PT=%d size=%d", packet.PayloadType, len(packet.Payload))
		}
		if packetCount > 0 && packet.Timestamp != lastTimestamp+lastDuration {
			t.Fatalf("Opus RTP timestamp = %d, want contiguous timestamp %d", packet.Timestamp, lastTimestamp+lastDuration)
		}
		if packetCount == 0 && packet.Timestamp != firstTimestamp {
			t.Fatalf("first Opus timestamp = %d, want source timestamp %d", packet.Timestamp, firstTimestamp)
		}
		duration := mediopus.PacketDuration2(packet.Payload)
		if duration <= 0 || duration > 5760 {
			t.Fatalf("invalid Opus RTP packet duration: %d", duration)
		}
		lastTimestamp = packet.Timestamp
		lastDuration = uint32(duration)
		n, err := decoder.DecodeToInt16(packet.Payload, samples)
		if err != nil {
			t.Fatalf("decode generated Opus packet: %v", err)
		}
		if int64(n) != duration {
			t.Fatalf("decoded sample duration=%d, RTP packet duration=%d", n, duration)
		}
		for _, sample := range samples[:n*2] {
			if sample != 0 {
				audible = true
				break
			}
		}
		packetCount++
	}
	if packetCount < 10 || !audible {
		t.Fatalf("converted audio packets=%d audible=%t", packetCount, audible)
	}
}

func TestAACToOpusSourceTimestampDiscontinuities(t *testing.T) {
	ffmpeg := os.Getenv("NETVUE_TEST_FFMPEG_FULL")
	if ffmpeg == "" {
		t.Skip("set NETVUE_TEST_FFMPEG_FULL to test AAC-to-Opus conversion")
	}
	accessUnits := syntheticAACAccessUnits(t, ffmpeg, 1)
	if len(accessUnits) < 4 {
		t.Fatalf("generated only %d AAC access units", len(accessUnits))
	}
	audio := &format.MPEG4Audio{Config: &mpeg4audio.AudioSpecificConfig{
		Type: mpeg4audio.ObjectTypeAACLC, SampleRate: 8000, ChannelConfig: 1,
	}}
	adtsFrames := make([][]byte, 0, len(accessUnits))
	for _, unit := range accessUnits {
		adts, err := aacAccessUnitToADTS(audio, unit)
		if err != nil {
			t.Fatal(err)
		}
		adtsFrames = append(adtsFrames, adts)
	}
	run := func(t *testing.T, first uint32, shift int64) []uint32 {
		t.Helper()
		source := make(chan aacRTPFrame, len(adtsFrames))
		for i, frame := range adtsFrames {
			timestamp := int64(first) + int64(i*1024*48000/8000)
			if i >= len(adtsFrames)/2 {
				timestamp += shift
			}
			source <- aacRTPFrame{data: frame, timestamp: uint32(timestamp)}
		}
		close(source)
		track := &capturedRTPTrack{packets: make(chan *rtp.Packet, 128)}
		ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
		defer cancel()
		if err := transcodeAACToOpusRTP(ctx, ffmpeg, source, track); err != nil {
			t.Fatal(err)
		}
		close(track.packets)
		var timestamps []uint32
		var next uint32
		for packet := range track.packets {
			if len(timestamps) == 0 {
				if packet.Timestamp != first {
					t.Fatalf("first RTP timestamp = %d, want %d", packet.Timestamp, first)
				}
			} else if packet.Timestamp != next {
				t.Fatalf("RTP timestamp = %d, want contiguous %d", packet.Timestamp, next)
			}
			duration := mediopus.PacketDuration2(packet.Payload)
			if duration <= 0 || duration > 5760 {
				t.Fatalf("invalid Opus duration: %d", duration)
			}
			next = packet.Timestamp + uint32(duration)
			timestamps = append(timestamps, packet.Timestamp)
		}
		if len(timestamps) < 10 {
			t.Fatalf("received only %d Opus packets", len(timestamps))
		}
		return timestamps
	}

	const first = uint32(123456)
	baseline := run(t, first, 0)
	for _, test := range []struct {
		name  string
		first uint32
		shift int64
	}{
		{name: "forward one second", first: first, shift: 48000},
		{name: "backward", first: first, shift: -10000},
		{name: "natural wrap", first: ^uint32(0) - 3000},
	} {
		t.Run(test.name, func(t *testing.T) {
			got := run(t, test.first, test.shift)
			if len(got) != len(baseline) {
				t.Fatalf("Opus packet count = %d, baseline = %d", len(got), len(baseline))
			}
			for i := range got {
				if got[i]-test.first != baseline[i]-first {
					t.Fatalf("packet %d offset = %d, baseline = %d",
						i, got[i]-test.first, baseline[i]-first)
				}
			}
			if test.name == "natural wrap" && got[len(got)-1] >= got[0] {
				t.Fatal("test did not cross the uint32 RTP timestamp wrap")
			}
			t.Logf("AAC timestamp shift=%d ticks; Opus span=%d ticks, baseline span=%d ticks",
				test.shift, got[len(got)-1]-test.first, baseline[len(baseline)-1]-first)
		})
	}
}

func syntheticAACAccessUnits(t *testing.T, ffmpeg string, seconds int) [][]byte {
	t.Helper()
	input := exec.Command(ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
		"-i", fmt.Sprintf("sine=frequency=700:sample_rate=8000:duration=%d", seconds),
		"-ac", "1", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "16k",
		"-f", "adts", "pipe:1")
	data, err := input.Output()
	if err != nil {
		t.Fatalf("generate synthetic AAC: %v", err)
	}
	frames, err := parseTestADTS(data)
	if err != nil {
		t.Fatal(err)
	}
	return frames
}

func parseTestADTS(data []byte) ([][]byte, error) {
	var frames [][]byte
	for len(data) != 0 {
		if len(data) < 7 || data[0] != 0xff || data[1]&0xf6 != 0xf0 || data[1]&1 == 0 {
			return nil, errors.New("invalid synthetic ADTS stream")
		}
		length := int(data[3]&3)<<11 | int(data[4])<<3 | int(data[5]>>5)
		if length < 8 || length > len(data) {
			return nil, errors.New("truncated synthetic ADTS frame")
		}
		frames = append(frames, append([]byte(nil), data[7:length]...))
		data = data[length:]
	}
	return frames, nil
}
