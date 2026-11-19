package netvue

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/h264"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/mpeg4audio"
	"github.com/mtx26/netvue-ha/internal/netvue/netvuetest"
)

func TestMediaMTXContinuousReaderResolutionChange(t *testing.T) {
	mediamtx := netvuetest.Executable(t, "NETVUE_TEST_MEDIAMTX", "mediamtx")
	ffmpeg := netvuetest.Executable(t, "NETVUE_TEST_FFMPEG", "ffmpeg")
	dir := t.TempDir()
	small := multiCameraFrames(t, ffmpeg, filepath.Join(dir, "small.ts"), "red", "160x90")
	large := multiCameraFrames(t, ffmpeg, filepath.Join(dir, "large.ts"), "blue", "320x180")
	audioPath := filepath.Join(dir, "audio.aac")
	generateSyntheticAudio(t, ffmpeg, audioPath)
	audioBytes, err := os.ReadFile(audioPath)
	if err != nil {
		t.Fatal(err)
	}
	var adts mpeg4audio.ADTSPackets
	if err := adts.Unmarshal(audioBytes); err != nil || len(adts) < 10 {
		t.Fatalf("read AAC: %v, frames=%d", err, len(adts))
	}
	audioFrames := make([][]byte, len(adts))
	for i, packet := range adts {
		audioFrames[i], err = (mpeg4audio.ADTSPackets{packet}).Marshal()
		if err != nil {
			t.Fatal(err)
		}
	}
	port := startMediaMTX(t, mediamtx, filepath.Join(dir, "mediamtx.yml"))
	url := fmt.Sprintf("rtsp://127.0.0.1:%d/change", port)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	publisher, err := newVideoPublisher(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	defer publisher.Close()
	params := &audioParameters{sampleRate: adts[0].SampleRate, channels: adts[0].ChannelCount}
	if err := publisher.PushAudio(audioFrame{payload: audioFrames[0]}, params); err != nil {
		t.Fatal(err)
	}
	if err := publisher.Push(small[0], 0); err != nil {
		t.Fatal(err)
	}
	if publisher.Generation() != 1 {
		t.Fatalf("initial generation: %d", publisher.Generation())
	}

	command := exec.CommandContext(ctx, ffmpeg, "-hide_banner", "-loglevel", "info",
		"-rtsp_transport", "tcp", "-analyzeduration", "500000", "-probesize", "32768",
		"-i", url, "-map", "0:v:0", "-map", "0:a:0", "-vf", "showinfo", "-af", "ashowinfo",
		"-frames:v", "35", "-f", "null", "-")
	stderr, err := command.StderrPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	lines := make(chan string, 4096)
	scanDone := make(chan string, 1)
	go func() {
		var output strings.Builder
		scanner := bufio.NewScanner(stderr)
		for scanner.Scan() {
			line := scanner.Text()
			output.WriteString(line + "\n")
			select {
			case lines <- line:
			default:
			}
		}
		scanDone <- output.String()
	}()
	producerDone := make(chan error, 1)
	switchVideo := make(chan struct{})
	go func() {
		frames := small
		switchCh := switchVideo
		videoIndex, audioIndex := 1, 0
		start := time.Now()
		for frameNumber := 1; frameNumber < 140; frameNumber++ {
			select {
			case <-ctx.Done():
				producerDone <- nil
				return
			case <-switchCh:
				frames = large
				videoIndex = 0
				switchCh = nil
			default:
			}
			deadline := start.Add(time.Duration(frameNumber) * 100 * time.Millisecond)
			timer := time.NewTimer(time.Until(deadline))
			select {
			case <-timer.C:
			case <-ctx.Done():
				timer.Stop()
				producerDone <- nil
				return
			}
			timestamp := uint32(frameNumber * 100)
			if err := publisher.Push(frames[videoIndex%len(frames)], timestamp); err != nil {
				producerDone <- err
				return
			}
			videoIndex++
			for audioIndex*1024*1000/params.sampleRate <= int(timestamp) {
				at := uint32(audioIndex * 1024 * 1000 / params.sampleRate)
				if err := publisher.PushAudio(audioFrame{timestamp: at, payload: audioFrames[audioIndex%len(audioFrames)]}, params); err != nil {
					producerDone <- err
					return
				}
				audioIndex++
			}
		}
		producerDone <- nil
	}()
	waitDone := make(chan error, 1)
	go func() { waitDone <- command.Wait() }()
	var oldVideo, oldAudio, newVideo, newAudio, switched bool
	for !newVideo || !newAudio {
		select {
		case line := <-lines:
			if strings.Contains(line, "Parsed_showinfo") && strings.Contains(line, " s:160x90 ") {
				oldVideo = true
			}
			if strings.Contains(line, "Parsed_ashowinfo") && strings.Contains(line, " n:") {
				if newVideo {
					newAudio = true
				} else {
					oldAudio = true
				}
			}
			if oldVideo && oldAudio && !switched {
				close(switchVideo)
				switched = true
			}
			if strings.Contains(line, "Parsed_showinfo") && strings.Contains(line, " s:320x180 ") {
				newVideo = true
			}
		case err := <-waitDone:
			cancel()
			<-producerDone
			t.Fatalf("FFmpeg exited before both resolutions and audio: %v; output: %s", err, <-scanDone)
		case err := <-producerDone:
			cancel()
			<-waitDone
			t.Fatalf("producer stopped before both resolutions and audio: %v; output: %s", err, <-scanDone)
		case <-ctx.Done():
			<-producerDone
			<-waitDone
			t.Fatalf("timed out waiting for resolution and audio change: %s", <-scanDone)
		}
	}
	// A later reader must receive the updated SPS in its DESCRIBE response.
	describeErr := func() error {
		u, err := base.ParseURL(url)
		if err != nil {
			return err
		}
		client := &gortsplib.Client{Scheme: u.Scheme, Host: u.Host, ReadTimeout: 2 * time.Second, WriteTimeout: 2 * time.Second}
		if err := client.Start(); err != nil {
			return err
		}
		defer client.Close()
		desc, _, err := client.Describe(u)
		if err != nil {
			return fmt.Errorf("late reader DESCRIBE: %w", err)
		}
		var videoFormat *format.H264
		if desc.FindFormat(&videoFormat) == nil {
			return fmt.Errorf("late reader has no H.264 track")
		}
		spsBytes, _ := videoFormat.SafeParams()
		var sps h264.SPS
		if err := sps.Unmarshal(spsBytes); err != nil || sps.Width() != 320 || sps.Height() != 180 {
			return fmt.Errorf("late reader SPS dimensions=%dx%d error=%v", sps.Width(), sps.Height(), err)
		}
		return nil
	}()
	select {
	case err := <-waitDone:
		if err != nil {
			cancel()
			<-producerDone
			t.Fatalf("FFmpeg interrupted: %v; output: %s", err, <-scanDone)
		}
	case <-ctx.Done():
		<-producerDone
		t.Fatalf("FFmpeg did not finish: %s", <-scanDone)
	}
	cancel()
	if err := <-producerDone; err != nil {
		t.Fatal(err)
	}
	if publisher.Generation() != 1 {
		t.Fatalf("publication restarted, generation=%d", publisher.Generation())
	}
	output := <-scanDone
	if describeErr != nil {
		t.Fatal(describeErr)
	}
	if !oldVideo || !oldAudio || !newVideo || !newAudio {
		t.Fatalf("missing decoded video/audio: %s", output)
	}
	if strings.Contains(output, "Error while decoding") {
		t.Fatalf("FFmpeg decode error: %s", output)
	}
}
