package netvue

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/gortsplib/v5/pkg/format/rtph264"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/h264"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/mpeg4audio"
	"github.com/bluenviron/mediacommon/v2/pkg/formats/mpegts"
	tscodecs "github.com/bluenviron/mediacommon/v2/pkg/formats/mpegts/codecs"
	"github.com/mtx26/netvue-ha/internal/netvue/netvuetest"
	"github.com/pion/rtp"
)

func TestPublisherRTPPresentationTimestampsOverRTSP(t *testing.T) {
	handler := &publisherTestRTSPHandler{
		recorded: make(chan struct{}, 1),
		closed:   make(chan struct{}, 1),
		packets:  make(chan observedRTP, 32),
	}
	server := &gortsplib.Server{Handler: handler, RTSPAddress: "127.0.0.1:0"}
	handler.server = server
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	port := server.NetListener().Addr().(*net.TCPAddr).Port
	publisher, err := newVideoPublisher(context.Background(), fmt.Sprintf("rtsp://127.0.0.1:%d/test", port))
	if err != nil {
		t.Fatal(err)
	}
	defer publisher.Close()

	au := &mpeg4audio.ADTSPacket{
		Type:          mpeg4audio.ObjectTypeAACLC,
		SampleRate:    8000,
		ChannelConfig: 1,
		AU:            []byte{1, 2, 3},
	}
	multi, err := (mpeg4audio.ADTSPackets{au, au}).Marshal()
	if err != nil {
		t.Fatal(err)
	}
	single, err := (mpeg4audio.ADTSPackets{au}).Marshal()
	if err != nil {
		t.Fatal(err)
	}
	parameters := &audioParameters{sampleRate: 8000, channels: 1}
	if err := publisher.PushAudio(audioFrame{timestamp: 1000, payload: multi}, parameters); err != nil {
		t.Fatal(err)
	}
	idr := []byte{
		0, 0, 0, 1, 0x67, 0x42, 0, 0x1e, 0xf4, 0x21, 0x32,
		0, 0, 0, 1, 0x68, 0xc0,
		0, 0, 0, 1, 0x65, 0x80,
	}
	if err := publisher.Push(idr, 1000); err != nil {
		t.Fatal(err)
	}
	waitForPublisherRecord(t, handler.recorded, 1)
	if err := publisher.PushAudio(audioFrame{timestamp: 1000, payload: multi}, parameters); err != nil {
		t.Fatal(err)
	}
	if err := publisher.PushAudio(audioFrame{timestamp: 1064, payload: single}, parameters); err != nil {
		t.Fatal(err)
	}
	if err := publisher.Push([]byte{0, 0, 0, 1, 0x41, 0x80}, 1010); err != nil {
		t.Fatal(err)
	}

	var video, audio []uint32
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	for len(video) < 2 || len(audio) < 3 {
		select {
		case packet := <-handler.packets:
			switch packet.media {
			case description.MediaTypeVideo:
				if packet.marker {
					video = append(video, packet.timestamp)
				}
			case description.MediaTypeAudio:
				audio = append(audio, packet.timestamp)
			}
		case <-deadline.C:
			t.Fatalf("timed out receiving RTP: video=%v audio=%v", video, audio)
		}
	}
	if got := video[1] - video[0]; got != 900 {
		t.Fatalf("video RTP PTS delta = %d, want 900 ticks (10 ms)", got)
	}
	for i := 1; i < len(audio); i++ {
		if got := audio[i] - audio[i-1]; got != 1024 {
			t.Fatalf("audio RTP PTS delta %d = %d, want 1024 samples", i, got)
		}
	}
}

func TestPublisherWaitsForIDRAfterParameterChangesWithoutRestart(t *testing.T) {
	handler := &publisherTestRTSPHandler{
		recorded: make(chan struct{}, 3),
		closed:   make(chan struct{}, 3),
		packets:  make(chan observedRTP, 32),
	}
	server := &gortsplib.Server{Handler: handler, RTSPAddress: "127.0.0.1:0"}
	handler.server = server
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	port := server.NetListener().Addr().(*net.TCPAddr).Port

	publisher, err := newVideoPublisher(context.Background(),
		fmt.Sprintf("rtsp://127.0.0.1:%d/test", port))
	if err != nil {
		t.Fatal(err)
	}
	defer publisher.Close()
	publisher.sps = []byte{0x67, 0x42, 0x00, 0x1e, 0xf4, 0x21, 0x32}
	publisher.pps = []byte{0x68, 0xc0}
	au := &mpeg4audio.ADTSPacket{Type: mpeg4audio.ObjectTypeAACLC, SampleRate: 8000, ChannelConfig: 1, AU: []byte{1, 2, 3}}
	audioPayload, err := (mpeg4audio.ADTSPackets{au}).Marshal()
	if err != nil {
		t.Fatal(err)
	}
	audioParams := &audioParameters{sampleRate: 8000, channels: 1}
	if err := publisher.PushAudio(audioFrame{payload: audioPayload}, audioParams); err != nil {
		t.Fatal(err)
	}

	if err := publisher.start(); err != nil {
		t.Fatalf("complete RTSP ANNOUNCE/SETUP/RECORD: %v", err)
	}
	waitForPublisherRecord(t, handler.recorded, 1)

	configurations := []struct {
		sps, pps []byte
	}{
		{[]byte{0x67, 0x42, 0, 0x1e, 0xf4, 0x10, 0x4c, 0x80}, []byte{0x68, 0xd0}},
		{[]byte{0x67, 0x42, 0, 0x1e, 0xf4, 0x20, 0x8c, 0x80}, []byte{0x68, 0xe0}},
	}
	for index, configuration := range configurations {
		parameters := append([]byte{0, 0, 0, 1}, configuration.sps...)
		parameters = append(parameters, 0, 0, 0, 1)
		parameters = append(parameters, configuration.pps...)
		if err := publisher.Push(parameters, uint32((index+1)*33)); err != nil {
			t.Fatalf("publish changed SPS/PPS: %v", err)
		}
		if !publisher.started || !publisher.waitingIDR || publisher.Generation() != 1 {
			t.Fatal("publisher did not keep its session while waiting for IDR")
		}
		select {
		case <-handler.closed:
			t.Fatal("RTSP session closed after parameter change")
		default:
		}
		before := publisher.PublishedFrames()
		if err := publisher.Push([]byte{0, 0, 0, 1, 0x41, 0x80}, uint32((index+2)*33)); err != nil {
			t.Fatalf("non-IDR while waiting: %v", err)
		}
		if publisher.PublishedFrames() != before || !publisher.waitingIDR {
			t.Fatal("published non-IDR before the new IDR")
		}
		if err := publisher.PushAudio(audioFrame{timestamp: uint32((index + 2) * 33), payload: audioPayload}, audioParams); err != nil {
			t.Fatalf("audio while waiting for IDR: %v", err)
		}
		select {
		case packet := <-handler.packets:
			if packet.media != description.MediaTypeAudio {
				t.Fatal("non-IDR video was published while waiting")
			}
		case <-time.After(time.Second):
			t.Fatal("audio stopped while waiting for IDR")
		}

		if err := publisher.Push([]byte{0, 0, 0, 1, 0x65, 0x80}, uint32((index+3)*33)); err != nil {
			t.Fatalf("publish IDR after parameter change: %v", err)
		}
		if publisher.waitingIDR || publisher.Generation() != 1 || publisher.PublishedFrames() != before+1 {
			t.Fatal("IDR did not resume the existing publication")
		}
		decoder := &rtph264.Decoder{PacketizationMode: 1}
		if err := decoder.Init(); err != nil {
			t.Fatal(err)
		}
		var decoded [][]byte
		deadline := time.After(time.Second)
		for len(decoded) == 0 {
			select {
			case packet := <-handler.packets:
				if packet.media != description.MediaTypeVideo {
					t.Fatal("unexpected audio after IDR")
				}
				var decodeErr error
				decoded, decodeErr = decoder.Decode(packet.packet)
				if decodeErr != nil && !errors.Is(decodeErr, rtph264.ErrMorePacketsNeeded) {
					t.Fatal(decodeErr)
				}
			case <-deadline:
				t.Fatal("IDR not received")
			}
		}
		if len(decoded) != 3 || !bytes.Equal(decoded[0], configuration.sps) ||
			!bytes.Equal(decoded[1], configuration.pps) || decoded[2][0]&0x1f != 5 {
			t.Fatalf("IDR did not carry current SPS/PPS: %x", decoded)
		}
	}
}

func TestPublisherClassifiesRTSPFailure(t *testing.T) {
	for _, method := range []string{"ANNOUNCE", "SETUP", "RECORD"} {
		for _, test := range []struct {
			name      string
			status    base.StatusCode
			wantError error
			retryable bool
		}{
			{name: "unavailable", status: base.StatusServiceUnavailable, wantError: ErrVideoStartup, retryable: true},
			{name: "forbidden", status: base.StatusForbidden, wantError: ErrVideoFatal},
		} {
			t.Run(method+"/"+test.name, func(t *testing.T) {
				handler := &publisherTestRTSPHandler{
					recorded: make(chan struct{}, 1),
					closed:   make(chan struct{}, 1),
					failAt:   method,
					status:   test.status,
				}
				server := &gortsplib.Server{Handler: handler, RTSPAddress: "127.0.0.1:0"}
				handler.server = server
				if err := server.Start(); err != nil {
					t.Fatal(err)
				}
				defer server.Close()
				port := server.NetListener().Addr().(*net.TCPAddr).Port
				publisher, err := newVideoPublisher(context.Background(), fmt.Sprintf("rtsp://127.0.0.1:%d/test", port))
				if err != nil {
					t.Fatal(err)
				}
				defer publisher.Close()
				publisher.sps = []byte{0x67, 0x42, 0, 0x1e, 0xf4, 0x21, 0x32}
				publisher.pps = []byte{0x68, 0xc0}
				err = publisher.start()
				if !errors.Is(err, test.wantError) || retryableSessionError(err) != test.retryable {
					t.Fatalf("%d at %s: error = %v, want %v (retryable=%t)", test.status, method, err, test.wantError, test.retryable)
				}
			})
		}
	}
}

func waitForPublisherRecord(t *testing.T, recorded <-chan struct{}, generation int) {
	t.Helper()
	select {
	case <-recorded:
	case <-time.After(time.Second):
		t.Fatalf("RTSP server did not receive RECORD for generation %d", generation)
	}
}

type publisherTestRTSPHandler struct {
	server   *gortsplib.Server
	stream   *gortsplib.ServerStream
	recorded chan struct{}
	closed   chan struct{}
	packets  chan observedRTP
	failAt   string
	status   base.StatusCode
}

type observedRTP struct {
	media     description.MediaType
	timestamp uint32
	marker    bool
	packet    *rtp.Packet
}

func (handler *publisherTestRTSPHandler) OnAnnounce(
	ctx *gortsplib.ServerHandlerOnAnnounceCtx,
) (*base.Response, error) {
	if handler.failAt == "ANNOUNCE" {
		return &base.Response{StatusCode: handler.status}, nil
	}
	handler.stream = &gortsplib.ServerStream{Server: handler.server, Desc: ctx.Description}
	if err := handler.stream.Initialize(); err != nil {
		return &base.Response{StatusCode: base.StatusInternalServerError}, err
	}
	return &base.Response{StatusCode: base.StatusOK}, nil
}

func (handler *publisherTestRTSPHandler) OnSetup(
	*gortsplib.ServerHandlerOnSetupCtx,
) (*base.Response, *gortsplib.ServerStream, error) {
	if handler.failAt == "SETUP" {
		return &base.Response{StatusCode: handler.status}, nil, nil
	}
	return &base.Response{StatusCode: base.StatusOK}, nil, nil
}

func (handler *publisherTestRTSPHandler) OnRecord(
	ctx *gortsplib.ServerHandlerOnRecordCtx,
) (*base.Response, error) {
	if handler.failAt == "RECORD" {
		return &base.Response{StatusCode: handler.status}, nil
	}
	if handler.packets != nil {
		ctx.Session.OnPacketRTPAny(func(media *description.Media, _ format.Format, packet *rtp.Packet) {
			handler.packets <- observedRTP{media: media.Type, timestamp: packet.Timestamp, marker: packet.Marker, packet: packet.Clone()}
		})
	}
	handler.recorded <- struct{}{}
	return &base.Response{StatusCode: base.StatusOK}, nil
}

func (handler *publisherTestRTSPHandler) OnSessionClose(*gortsplib.ServerHandlerOnSessionCloseCtx) {
	if handler.stream != nil {
		handler.stream.Close()
	}
	handler.closed <- struct{}{}
}

func TestMediaMTXPublicationDecodesWithFFmpeg(t *testing.T) {
	mediamtx := netvuetest.Executable(t, "NETVUE_TEST_MEDIAMTX", "mediamtx")
	ffmpeg := netvuetest.Executable(t, "NETVUE_TEST_FFMPEG", "ffmpeg")
	dir := t.TempDir()
	videoPath := filepath.Join(dir, "synthetic.ts")
	generateSyntheticVideo(t, ffmpeg, videoPath)
	port := startMediaMTX(t, mediamtx, filepath.Join(dir, "mediamtx.yml"))
	publishURL := fmt.Sprintf("rtsp://127.0.0.1:%d/synthetic", port)
	publisher, err := newVideoPublisher(context.Background(), publishURL)
	if err != nil {
		t.Fatal(err)
	}
	defer publisher.Close()

	file, err := os.Open(videoPath)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	type probeResult struct {
		output string
		err    error
	}
	probeDone := make(chan probeResult, 1)
	var probeStarted bool
	startTime := time.Time{}
	var firstPTS int64
	timeDecoder := mpegts.NewTimeDecoder()
	err = readH264Frames(file, func(pts int64, data []byte) error {
		pts = timeDecoder.Decode(pts)
		if startTime.IsZero() {
			startTime = time.Now()
			firstPTS = pts
		}
		relativePTS := pts - firstPTS
		targetTime := startTime.Add(time.Duration(relativePTS) * time.Second / 90_000)
		if delay := time.Until(targetTime); delay > 0 {
			time.Sleep(delay)
		}
		timestamp := uint32(relativePTS / 90)
		if err := publisher.Push(data, timestamp); err != nil {
			return err
		}
		if publisher.PublishedFrames() > 0 && !probeStarted {
			probeStarted = true
			go func() {
				ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
				defer cancel()
				command := exec.CommandContext(ctx, ffmpeg,
					"-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats",
					"-rtsp_transport", "tcp", "-i", publishURL, "-frames:v", "10",
					"-f", "null", "-",
				)
				output, err := command.CombinedOutput()
				probeDone <- probeResult{output: string(output), err: err}
			}()
		}
		return nil
	})
	if err != nil {
		t.Fatalf("read synthetic H.264 stream: %v", err)
	}
	if !probeStarted {
		t.Fatal("the publisher did not start")
	}
	publisher.Close()
	select {
	case result := <-probeDone:
		if result.err != nil {
			t.Fatalf("independent FFmpeg decode failed: %v; output: %s", result.err, result.output)
		}
		if !strings.Contains(result.output, "frame=10") {
			t.Fatalf("FFmpeg did not confirm ten decoded frames: %s", result.output)
		}
	case <-time.After(9 * time.Second):
		t.Fatal("timed out waiting for the independent FFmpeg reader")
	}
	if publisher.PublishedFrames() < 10 {
		t.Fatalf("published only %d access units", publisher.PublishedFrames())
	}
}

func TestMediaMTXAudioVideoPublicationDecodesWithFFmpeg(t *testing.T) {
	mediamtx := netvuetest.Executable(t, "NETVUE_TEST_MEDIAMTX", "mediamtx")
	ffmpeg := netvuetest.Executable(t, "NETVUE_TEST_FFMPEG", "ffmpeg")
	ffprobe := os.Getenv("NETVUE_TEST_FFPROBE")
	dir := t.TempDir()
	videoPath := filepath.Join(dir, "synthetic.ts")
	audioPath := filepath.Join(dir, "synthetic.aac")
	generateSyntheticVideo(t, ffmpeg, videoPath)
	generateSyntheticAudio(t, ffmpeg, audioPath)
	audioData, err := os.ReadFile(audioPath)
	if err != nil {
		t.Fatal(err)
	}
	var adts mpeg4audio.ADTSPackets
	if err := adts.Unmarshal(audioData); err != nil || len(adts) < 10 {
		t.Fatalf("read synthetic AAC frames: count=%d err=%v", len(adts), err)
	}
	audioFrames := make([][]byte, len(adts))
	for index, packet := range adts {
		audioFrames[index], err = (mpeg4audio.ADTSPackets{packet}).Marshal()
		if err != nil {
			t.Fatalf("marshal synthetic AAC frame: %v", err)
		}
	}
	port := startMediaMTX(t, mediamtx, filepath.Join(dir, "mediamtx.yml"))
	publishURL := fmt.Sprintf("rtsp://127.0.0.1:%d/synthetic-av", port)
	publisher, err := newVideoPublisher(context.Background(), publishURL)
	if err != nil {
		t.Fatal(err)
	}
	defer publisher.Close()

	audioParameters := &audioParameters{sampleRate: adts[0].SampleRate, sampleSize: 16, channels: adts[0].ChannelCount}

	file, err := os.Open(videoPath)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	type probeResult struct {
		output string
		err    error
	}
	probeDone := make(chan probeResult, 1)
	ffprobeDone := make(chan probeResult, 1)
	var probeStarted bool
	startTime := time.Time{}
	var firstPTS int64
	timeDecoder := mpegts.NewTimeDecoder()
	audioIndex := 0
	err = readH264Frames(file, func(pts int64, data []byte) error {
		pts = timeDecoder.Decode(pts)
		if startTime.IsZero() {
			startTime = time.Now()
			firstPTS = pts
		}
		relativePTS := pts - firstPTS
		targetTime := startTime.Add(time.Duration(relativePTS) * time.Second / 90_000)
		if delay := time.Until(targetTime); delay > 0 {
			time.Sleep(delay)
		}
		timestamp := uint32(relativePTS / 90)
		if err := publisher.Push(data, timestamp); err != nil {
			return err
		}
		for audioIndex < len(audioFrames) &&
			int64(audioIndex*1024*1000/audioParameters.sampleRate) <= relativePTS/90 &&
			publisher.CurrentPublicationFrames() > 0 {
			audioTimeMS := uint32(audioIndex * 1024 * 1000 / audioParameters.sampleRate)
			if err := publisher.PushAudio(audioFrame{
				timestamp: audioTimeMS,
				payload:   audioFrames[audioIndex],
			}, audioParameters); err != nil {
				return err
			}
			audioIndex++
		}
		if publisher.Generation() > 1 && publisher.CurrentPublicationFrames() > 0 && !probeStarted {
			probeStarted = true
			if ffprobe != "" {
				go func() {
					ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
					defer cancel()
					command := exec.CommandContext(ctx, ffprobe,
						"-v", "error", "-rtsp_transport", "tcp",
						"-analyzeduration", "500000", "-probesize", "32768",
						"-show_entries", "stream=codec_name,codec_type", "-of", "json", publishURL,
					)
					output, err := command.CombinedOutput()
					ffprobeDone <- probeResult{output: string(output), err: err}
				}()
			}
			go func() {
				ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
				defer cancel()
				command := exec.CommandContext(ctx, ffmpeg,
					"-hide_banner", "-loglevel", "info", "-progress", "pipe:1", "-nostats",
					"-rtsp_transport", "tcp", "-analyzeduration", "500000", "-probesize", "32768",
					"-i", publishURL,
					"-map", "0:v:0", "-map", "0:a:0",
					"-frames:v", "10", "-f", "null", "-",
				)
				output, err := command.CombinedOutput()
				probeDone <- probeResult{output: string(output), err: err}
			}()
		}
		return nil
	})
	if err != nil {
		t.Fatalf("read synthetic H.264 stream: %v", err)
	}
	if !probeStarted {
		t.Fatal("the publisher did not start")
	}
	if audioIndex < 10 {
		t.Fatalf("published only %d synthetic AAC frames", audioIndex)
	}
	if publisher.Generation() < 2 {
		t.Fatal("late audio track configuration did not restart RTSP publication")
	}
	if ffprobe != "" {
		select {
		case result := <-ffprobeDone:
			if result.err != nil {
				t.Fatalf("FFprobe RTSP inspection failed: %v; output: %s", result.err, result.output)
			}
			var metadata struct {
				Streams []struct {
					CodecName string `json:"codec_name"`
					CodecType string `json:"codec_type"`
				} `json:"streams"`
			}
			if err := json.Unmarshal([]byte(result.output), &metadata); err != nil {
				t.Fatalf("parse FFprobe output: %v; output: %s", err, result.output)
			}
			foundVideo, foundAudio := false, false
			for _, stream := range metadata.Streams {
				foundVideo = foundVideo || stream.CodecType == "video" && stream.CodecName == "h264"
				foundAudio = foundAudio || stream.CodecType == "audio" && stream.CodecName == "aac"
			}
			if !foundVideo || !foundAudio {
				t.Fatalf("FFprobe did not find both H.264 and AAC tracks: %s", result.output)
			}
		case <-time.After(9 * time.Second):
			t.Fatal("timed out waiting for FFprobe")
		}
	}
	publisher.Close()
	select {
	case result := <-probeDone:
		if result.err != nil {
			t.Fatalf("independent FFmpeg A/V decode failed: %v; output: %s", result.err, result.output)
		}
		if !strings.Contains(result.output, "frame=10") ||
			!strings.Contains(result.output, "Audio: aac") ||
			strings.Contains(result.output, "audio:0KiB") {
			t.Fatalf("FFmpeg did not confirm decoded H.264 and AAC: %s", result.output)
		}
	case <-time.After(9 * time.Second):
		t.Fatal("timed out waiting for the independent FFmpeg A/V reader")
	}
}

// readH264Frames extracts Annex-B access units from a transport stream and
// invokes onFrame synchronously as each frame is read.
func readH264Frames(file *os.File, onFrame func(pts int64, accessUnit []byte) error) error {
	reader, err := mpegts.NewReader(file)
	if err != nil {
		return fmt.Errorf("create MPEG-TS reader: %w", err)
	}
	var track *mpegts.Track
	for _, candidate := range reader.Tracks() {
		if _, ok := candidate.Codec.(*tscodecs.H264); ok {
			track = candidate
			break
		}
	}
	if track == nil {
		return errors.New("MPEG-TS stream has no H.264 track")
	}

	reader.OnDataH264(track, func(pts, _ int64, nalus [][]byte) error {
		accessUnit, err := (h264.AnnexB(nalus)).Marshal()
		if err != nil {
			return fmt.Errorf("marshal H.264 access unit: %w", err)
		}
		return onFrame(pts, accessUnit)
	})
	for {
		err := reader.Read()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return fmt.Errorf("read MPEG-TS stream: %w", err)
		}
	}
}

func generateSyntheticVideo(t *testing.T, ffmpeg, outputPath string) {
	t.Helper()
	command := exec.Command(ffmpeg,
		"-hide_banner", "-loglevel", "error",
		"-f", "lavfi", "-i", "testsrc=size=160x90:rate=10",
		"-frames:v", "60",
		"-pix_fmt", "yuv420p",
		"-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
		"-x264-params", "keyint=10:min-keyint=10:repeat-headers=1",
		"-f", "mpegts", outputPath,
	)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("generate synthetic H.264: %v; output: %s", err, output)
	}
}

func generateSyntheticAudio(t *testing.T, ffmpeg, outputPath string) {
	t.Helper()
	command := exec.Command(ffmpeg,
		"-hide_banner", "-loglevel", "error",
		"-f", "lavfi", "-i", "sine=frequency=660:sample_rate=16000",
		"-t", "6", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "24k",
		"-ar", "16000", "-ac", "1", "-f", "adts", outputPath,
	)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("generate synthetic AAC: %v; output: %s", err, output)
	}
}

func startMediaMTX(t *testing.T, executable, configPath string) int {
	t.Helper()
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	config := fmt.Sprintf(`logLevel: error
logDestinations: [stdout]
rtsp: true
rtspAddress: 127.0.0.1:%d
rtspTransports: [tcp]
rtspEncryption: "no"
rtmp: false
hls: false
webrtc: false
srt: false
moq: false
api: false
metrics: false
pprof: false
playback: false
pathDefaults:
  record: false
  overridePublisher: false
paths:
  all_others:
`, port)
	if err := os.WriteFile(configPath, []byte(config), 0600); err != nil {
		t.Fatal(err)
	}
	var logs bytes.Buffer
	command := exec.Command(executable, configPath)
	command.Stdout = &logs
	command.Stderr = &logs
	if err := command.Start(); err != nil {
		t.Fatalf("start loopback MediaMTX: %v", err)
	}
	t.Cleanup(func() {
		_ = command.Process.Kill()
		_ = command.Wait()
		if t.Failed() {
			t.Logf("MediaMTX logs: %s", logs.String())
		}
	})

	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		conn, err := net.DialTimeout("tcp4", fmt.Sprintf("127.0.0.1:%d", port), 100*time.Millisecond)
		if err == nil {
			if closeErr := conn.Close(); closeErr != nil {
				t.Fatal(closeErr)
			}
			return port
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("MediaMTX did not open its loopback listener: %s", logs.String())
	return 0
}
