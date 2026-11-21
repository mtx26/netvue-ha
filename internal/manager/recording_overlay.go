package manager

import (
	"bufio"
	"context"
	"crypto/sha256"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

const overlayHold = 3 * time.Second

func (manager *Manager) recordingOverlaySamples(
	streamName string, start, end time.Time,
) []AIDetectionMetadata {
	return manager.aiMetadata.between(streamName, start.Add(-overlayHold), end)
}

func writeOverlayASS(w io.Writer, segment RecordingSegment, events []AIDetectionMetadata) error {
	writer := bufio.NewWriter(w)
	_, err := fmt.Fprint(writer, `[Script Info]
ScriptType: v4.00+
PlayResX: 10000
PlayResY: 10000
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Box,Arial,20,&HFF000000,&HFF000000,&H0000FF00,&HFF000000,0,0,0,0,100,100,0,0,1,35,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
`)
	if err != nil {
		return err
	}
	startSeconds := float64(segment.Start.UnixNano()) / float64(time.Second)
	for i, event := range events {
		start := event.Timestamp - startSeconds
		end := start + overlayHold.Seconds()
		if i+1 < len(events) {
			end = math.Min(end, events[i+1].Timestamp-startSeconds)
		}
		start = math.Max(0, start)
		end = math.Min(segment.Duration, end)
		if end <= start || len(event.Boxes) == 0 {
			continue
		}
		for _, box := range event.Boxes {
			x1 := int(math.Round(box.X1 * 10000 / float64(event.FrameWidth)))
			y1 := int(math.Round(box.Y1 * 10000 / float64(event.FrameHeight)))
			x2 := int(math.Round(box.X2 * 10000 / float64(event.FrameWidth)))
			y2 := int(math.Round(box.Y2 * 10000 / float64(event.FrameHeight)))
			_, err = fmt.Fprintf(writer,
				"Dialogue: 0,%s,%s,Box,,0,0,0,,{\\an7\\pos(0,0)\\p1}m %d %d l %d %d %d %d %d %d{\\p0}\n",
				overlayASSTime(start), overlayASSTime(end), x1, y1, x2, y1, x2, y2, x1, y2)
			if err != nil {
				return err
			}
		}
	}
	return writer.Flush()
}

func overlayASSTime(seconds float64) string {
	centiseconds := int64(math.Ceil(seconds * 100))
	return fmt.Sprintf("%d:%02d:%02d.%02d",
		centiseconds/360000, centiseconds/6000%60, centiseconds/100%60, centiseconds%100)
}

func (manager *Manager) renderRecordingOverlay(
	ctx context.Context, input *os.File, segment RecordingSegment, streamName string,
) (*os.File, string, error) {
	events := manager.recordingOverlaySamples(
		streamName, segment.Start,
		segment.Start.Add(time.Duration(segment.Duration*float64(time.Second))),
	)
	ffmpeg, err := manager.webRTCFFmpeg()
	if err != nil {
		return nil, "", err
	}
	dir, err := filepath.Abs(manager.recordings.dir)
	if err != nil {
		return nil, "", err
	}
	script, err := os.CreateTemp(dir, ".netvue-overlay-*.ass")
	if err != nil {
		return nil, "", err
	}
	defer os.Remove(script.Name())
	if err := writeOverlayASS(script, segment, events); err != nil {
		script.Close()
		return nil, "", err
	}
	if err := script.Close(); err != nil {
		return nil, "", err
	}
	output, err := os.CreateTemp(dir, ".netvue-overlay-*.tmp")
	if err != nil {
		return nil, "", err
	}
	success := false
	defer func() {
		if !success {
			output.Close()
			os.Remove(output.Name())
		}
	}()
	inputPath, err := filepath.Abs(input.Name())
	if err != nil {
		return nil, "", err
	}
	commandCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	cmd := exec.CommandContext(commandCtx, ffmpeg,
		"-hide_banner", "-loglevel", "error", "-threads", "2", "-i", inputPath,
		"-filter_threads", "1", "-vf", "ass="+filepath.Base(script.Name()),
		"-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-threads", "2",
		"-preset", "veryfast", "-crf", "23", "-c:a", "copy",
		"-movflags", "frag_keyframe+empty_moov", "-f", "mp4", "pipe:1")
	cmd.Dir = dir
	pipe, err := cmd.StdoutPipe()
	if err != nil {
		return nil, "", err
	}
	if err := cmd.Start(); err != nil {
		return nil, "", err
	}
	hash := sha256.New()
	size, copyErr := io.Copy(io.MultiWriter(output, hash), io.LimitReader(pipe, maxPlaybackBytes+1))
	if copyErr != nil || size > maxPlaybackBytes {
		cancel()
	}
	waitErr := cmd.Wait()
	if copyErr != nil {
		return nil, "", copyErr
	}
	if size > maxPlaybackBytes {
		return nil, "", fmt.Errorf("overlay recording exceeds playback size limit")
	}
	if waitErr != nil {
		return nil, "", waitErr
	}
	if _, err := output.Seek(0, io.SeekStart); err != nil {
		return nil, "", err
	}
	success = true
	return output, fmt.Sprintf(`"%x"`, hash.Sum(nil)), nil
}
