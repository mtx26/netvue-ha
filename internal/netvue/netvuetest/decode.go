package netvuetest

import (
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"os/exec"
	"time"
)

// DecodeVideoPixel decodes the first frames of the wanted geometry from a
// stream URL with an independent decoder (ffmpeg) and returns the RGB colour
// of the first one. The geometry filter rejects frames of any other size.
func DecodeVideoPixel(ctx context.Context, ffmpeg, url string, width, height int) ([3]byte, error) {
	filter := fmt.Sprintf(`select=eq(iw\,%d)*eq(ih\,%d),scale=1:1`, width, height)
	command := exec.CommandContext(ctx, ffmpeg, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
		"-i", url, "-an", "-vf", filter, "-frames:v", "2", "-fps_mode", "passthrough",
		"-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1")
	var stderr bytes.Buffer
	command.Stderr = &stderr
	output, err := command.Output()
	if err != nil || len(output) < 3 {
		return [3]byte{}, fmt.Errorf("decode %dx%d video from %s: %v (%d bytes): %s", width, height, url, err, len(output), stderr.String())
	}
	return [3]byte{output[0], output[1], output[2]}, nil
}

// IsRed and IsBlue classify a decoded pixel of the solid fixtures.
func IsRed(p [3]byte) bool  { return p[0] > 200 && p[1] < 60 && p[2] < 60 }
func IsBlue(p [3]byte) bool { return p[2] > 200 && p[0] < 60 && p[1] < 60 }

// DecodeAudioPeak decodes one second of audio from a stream URL and returns
// the peak sample amplitude (0 means silence).
func DecodeAudioPeak(ctx context.Context, ffmpeg, url string) (int, error) {
	command := exec.CommandContext(ctx, ffmpeg, "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
		"-i", url, "-vn", "-t", "1", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1")
	var stderr bytes.Buffer
	command.Stderr = &stderr
	output, err := command.Output()
	if err != nil || len(output) < 4000 {
		return 0, fmt.Errorf("decode audio from %s: %v (%d bytes): %s", url, err, len(output), stderr.String())
	}
	return PeakS16LE(output), nil
}

// PeakS16LE returns the largest absolute sample of little-endian 16-bit PCM.
func PeakS16LE(pcm []byte) int {
	peak := 0
	for i := 0; i+1 < len(pcm); i += 2 {
		v := int(int16(binary.LittleEndian.Uint16(pcm[i:])))
		if v < 0 {
			v = -v
		}
		if v > peak {
			peak = v
		}
	}
	return peak
}

// Within returns a context bounded by limit.
func Within(limit time.Duration) (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), limit)
}
