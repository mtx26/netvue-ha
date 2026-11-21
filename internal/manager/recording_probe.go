package manager

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os/exec"
	"strconv"
	"strings"
)

type protectedStream struct {
	CodecType     string `json:"codec_type"`
	CodecName     string `json:"codec_name"`
	Width         int    `json:"width"`
	Height        int    `json:"height"`
	PixelFormat   string `json:"pix_fmt"`
	SampleRate    string `json:"sample_rate"`
	Channels      int    `json:"channels"`
	ChannelLayout string `json:"channel_layout"`
	TimeBase      string `json:"time_base"`
	FrameRate     string `json:"r_frame_rate"`
	FrameCount    string `json:"nb_read_frames"`
}

func checkProtectedTimestamps(ctx context.Context, ffprobe, source string) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	cmd := exec.CommandContext(ctx, ffprobe, "-v", "error", "-select_streams", "v:0", "-show_frames",
		"-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", source)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	scanner := bufio.NewScanner(stdout)
	count := 0
	var last float64
	var parseErr error
	for scanner.Scan() {
		value := strings.TrimSpace(strings.SplitN(scanner.Text(), ",", 2)[0])
		if value == "" {
			continue
		}
		timestamp, err := strconv.ParseFloat(value, 64)
		if err != nil || math.IsNaN(timestamp) || math.IsInf(timestamp, 0) || (count > 0 && timestamp <= last) {
			parseErr = errors.New("recording timestamps are missing or not strictly increasing")
			break
		}
		last = timestamp
		count++
		if count > 1000000 {
			parseErr = errors.New("recording contains too many video frames")
			break
		}
	}
	if parseErr == nil {
		parseErr = scanner.Err()
	}
	if parseErr != nil {
		cancel()
	}
	waitErr := cmd.Wait()
	if parseErr != nil {
		return parseErr
	}
	if waitErr != nil {
		return fmt.Errorf("read recording timestamps: %w", waitErr)
	}
	if count == 0 {
		return errors.New("recording has no timestamped video frames")
	}
	return nil
}

func probeProtectedStreams(ctx context.Context, ffprobe, path string) ([]protectedStream, error) {
	cmd := exec.CommandContext(ctx, ffprobe, "-v", "error", "-count_frames", "-show_entries",
		"stream=codec_type,codec_name,width,height,pix_fmt,sample_rate,channels,channel_layout,time_base,r_frame_rate,nb_read_frames",
		"-of", "json", path)
	data, err := cmd.Output()
	if err != nil {
		return nil, err
	}
	var result struct {
		Streams []protectedStream `json:"streams"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, err
	}
	if len(result.Streams) == 0 || result.Streams[0].CodecType != "video" {
		return nil, errors.New("protected recording has no video stream")
	}
	frames, err := strconv.ParseUint(result.Streams[0].FrameCount, 10, 64)
	if err != nil || frames == 0 {
		return nil, errors.New("protected recording has no decodable video frames")
	}
	for i := range result.Streams {
		result.Streams[i].FrameCount = ""
	}
	return result.Streams, nil
}
