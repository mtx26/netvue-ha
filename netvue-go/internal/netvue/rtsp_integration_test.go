package netvue

import (
	"bytes"
	"context"
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
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/h264"
	"github.com/bluenviron/mediacommon/v2/pkg/formats/mpegts"
	tscodecs "github.com/bluenviron/mediacommon/v2/pkg/formats/mpegts/codecs"
)

func TestPublisherRestartsRecordingAfterParameterChanges(t *testing.T) {
	handler := &publisherTestRTSPHandler{
		recorded: make(chan struct{}, 3),
		closed:   make(chan struct{}, 3),
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
		if publisher.started {
			t.Fatal("publisher remained active after its SPS/PPS changed")
		}
		select {
		case <-handler.closed:
		case <-time.After(time.Second):
			t.Fatalf("RTSP server did not observe closure for generation %d", index+1)
		}

		if err := publisher.Push([]byte{0, 0, 0, 1, 0x65, 0x80}, uint32((index+2)*33)); err != nil {
			t.Fatalf("restart publication at IDR after parameter change: %v", err)
		}
		waitForPublisherRecord(t, handler.recorded, index+2)
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
}

func (handler *publisherTestRTSPHandler) OnAnnounce(
	ctx *gortsplib.ServerHandlerOnAnnounceCtx,
) (*base.Response, error) {
	handler.stream = &gortsplib.ServerStream{Server: handler.server, Desc: ctx.Description}
	if err := handler.stream.Initialize(); err != nil {
		return &base.Response{StatusCode: base.StatusInternalServerError}, err
	}
	return &base.Response{StatusCode: base.StatusOK}, nil
}

func (handler *publisherTestRTSPHandler) OnSetup(
	*gortsplib.ServerHandlerOnSetupCtx,
) (*base.Response, *gortsplib.ServerStream, error) {
	return &base.Response{StatusCode: base.StatusOK}, nil, nil
}

func (handler *publisherTestRTSPHandler) OnRecord(
	*gortsplib.ServerHandlerOnRecordCtx,
) (*base.Response, error) {
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
	mediamtx := os.Getenv("NETVUE_TEST_MEDIAMTX")
	ffmpeg := os.Getenv("NETVUE_TEST_FFMPEG")
	if mediamtx == "" || ffmpeg == "" {
		t.Skip("set NETVUE_TEST_MEDIAMTX and NETVUE_TEST_FFMPEG to run the loopback integration test")
	}
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
	reader, err := mpegts.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	var track *mpegts.Track
	for _, candidate := range reader.Tracks() {
		if _, ok := candidate.Codec.(*tscodecs.H264); ok {
			track = candidate
			break
		}
	}
	if track == nil {
		t.Fatal("synthetic transport stream has no H.264 track")
	}

	type probeResult struct {
		output string
		err    error
	}
	probeDone := make(chan probeResult, 1)
	var probeStarted bool
	startTime := time.Time{}
	var firstPTS int64
	timeDecoder := mpegts.NewTimeDecoder()
	reader.OnDataH264(track, func(pts, _ int64, accessUnit [][]byte) error {
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
		data, err := (h264.AnnexB(accessUnit)).Marshal()
		if err != nil {
			return err
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
	for {
		err := reader.Read()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			t.Fatalf("read synthetic H.264 stream: %v", err)
		}
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

func generateSyntheticVideo(t *testing.T, ffmpeg, outputPath string) {
	t.Helper()
	command := exec.Command(ffmpeg,
		"-hide_banner", "-loglevel", "error",
		"-f", "lavfi", "-i", "testsrc=size=160x90:rate=10",
		"-frames:v", "30",
		"-pix_fmt", "yuv420p",
		"-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
		"-x264-params", "keyint=10:min-keyint=10:repeat-headers=1",
		"-f", "mpegts", outputPath,
	)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("generate synthetic H.264: %v; output: %s", err, output)
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
