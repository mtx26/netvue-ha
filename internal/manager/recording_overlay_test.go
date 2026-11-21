package manager

import (
	"bytes"
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestOverlayUsesMostRecentSampleAndExpiresAcrossGaps(t *testing.T) {
	start := time.Unix(1_700_000_000, 0)
	segment := RecordingSegment{Start: start, Duration: 8}
	events := []AIDetectionMetadata{
		testAIMetadata("before", "camera", float64(start.Unix()-1)),
		{ID: "clear", Timestamp: float64(start.Unix() + 1), Event: "object_cleared"},
		testAIMetadata("after", "camera", float64(start.Unix()+2)),
		testAIMetadata("new", "camera", float64(start.Unix()+6)),
	}
	var script bytes.Buffer
	if err := writeOverlayASS(&script, segment, events); err != nil {
		t.Fatal(err)
	}
	for _, interval := range []string{
		"0:00:00.00,0:00:01.00", "0:00:02.00,0:00:05.00", "0:00:06.00,0:00:08.00",
	} {
		if !strings.Contains(script.String(), interval) {
			t.Errorf("missing interval %s in %s", interval, script.String())
		}
	}
	if count := strings.Count(script.String(), "Dialogue:"); count != 3 {
		t.Fatalf("got %d rectangles, want 3", count)
	}
}

func TestRecordingDownloadOverlayRendersAndPreservesAudio(t *testing.T) {
	ffmpeg := os.Getenv("NETVUE_TEST_FFMPEG_FULL")
	if ffmpeg == "" {
		t.Skip("set NETVUE_TEST_FFMPEG_FULL for synthetic overlay test")
	}
	dir := t.TempDir()
	input := filepath.Join(dir, "source.mp4")
	cmd := exec.CommandContext(t.Context(), ffmpeg, "-hide_banner", "-loglevel", "error",
		"-f", "lavfi", "-i", "color=black:s=128x72:r=10:d=6",
		"-f", "lavfi", "-i", "sine=frequency=440:duration=6",
		"-c:v", "libx264", "-threads", "2", "-pix_fmt", "yuv420p",
		"-c:a", "aac", "-shortest", input)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("create synthetic source: %v: %s", err, output)
	}
	data, err := os.ReadFile(input)
	if err != nil {
		t.Fatal(err)
	}
	start := time.Now().UTC().Truncate(time.Second)
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		fmt.Fprintf(w, `{"segments":[{"start":%q,"duration":6}]}`, start.Format(time.RFC3339))
	}))
	defer control.Close()
	playback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write(data)
	}))
	defer playback.Close()
	t.Setenv("NETVUE_RECORDINGS_DIR", dir)
	manager, err := newManager(t.Context(), "192.168.10.1", func(context.Context, string, string, string) error {
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	manager.recordings.controlURL = control.URL
	manager.recordings.playbackAddress = playback.URL
	manager.ffmpegPath = func() (string, error) { return ffmpeg, nil }
	manager.cameras["192.168.10.2"] = &cameraSession{Camera: Camera{
		Host: "192.168.10.2", StreamName: "camera",
	}}
	for _, event := range []AIDetectionMetadata{
		testAIMetadata("before", "camera", float64(start.Unix()-1)),
		{ID: "clear", StreamName: "camera", Timestamp: float64(start.Unix() + 1),
			Event: "object_cleared", FrameWidth: 1920, FrameHeight: 1080},
		testAIMetadata("after", "camera", float64(start.Unix()+2)),
	} {
		if len(event.Boxes) > 0 {
			event.Boxes[0] = AIDetectionBox{X1: 480, Y1: 270, X2: 1440, Y2: 810}
		}
		if _, err := manager.aiMetadata.add(event); err != nil {
			t.Fatal(err)
		}
	}
	path := "/api/v1/cameras/192.168.10.2/recordings/" + recordingID("camera", start) + "/download"
	partial := httptest.NewRequest(http.MethodGet, path+"?overlay=1", nil)
	partial.Header.Set("Range", "bytes=0-99")
	partialResult := httptest.NewRecorder()
	manager.Handler().ServeHTTP(partialResult, partial)
	if partialResult.Code != http.StatusBadRequest {
		t.Fatalf("partial annotated export = %d: %s", partialResult.Code, partialResult.Body)
	}
	manager.recordings.overlaySlot <- struct{}{}
	busy := httptest.NewRecorder()
	manager.Handler().ServeHTTP(busy, httptest.NewRequest(http.MethodGet, path+"?overlay=1", nil))
	<-manager.recordings.overlaySlot
	if busy.Code != http.StatusServiceUnavailable || busy.Header().Get("Retry-After") == "" {
		t.Fatalf("concurrent annotated export = %d: %s", busy.Code, busy.Body)
	}
	result := httptest.NewRecorder()
	manager.Handler().ServeHTTP(result, httptest.NewRequest(http.MethodGet, path+"?overlay=1", nil))
	if result.Code != http.StatusOK {
		t.Fatalf("overlay response = %d: %s", result.Code, result.Body.String())
	}
	if actual := result.Header().Get("Accept-Ranges"); actual != "none" {
		t.Fatalf("annotated export advertises ranges: %q", actual)
	}
	outputPath := filepath.Join(dir, "rendered.mp4")
	if err := os.WriteFile(outputPath, result.Body.Bytes(), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, sample := range []struct {
		seconds string
		box     bool
	}{{"0.5", true}, {"1.5", false}, {"2.5", true}, {"5.5", false}} {
		decode := exec.CommandContext(t.Context(), ffmpeg, "-hide_banner", "-loglevel", "error",
			"-ss", sample.seconds, "-i", outputPath, "-frames:v", "1",
			"-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
		pixels, err := decode.Output()
		if err != nil || len(pixels) != 128*72*3 {
			t.Fatalf("decode at %s: %v, %d bytes", sample.seconds, err, len(pixels))
		}
		green := false
		minX, minY, maxX, maxY := 128, 72, -1, -1
		for i := 0; i < len(pixels); i += 3 {
			if pixels[i+1] > 80 && int(pixels[i+1]) > int(pixels[i])*2 {
				green = true
				x, y := (i/3)%128, (i/3)/128
				minX, minY = min(minX, x), min(minY, y)
				maxX, maxY = max(maxX, x), max(maxY, y)
			}
		}
		if green != sample.box {
			t.Errorf("at %ss rectangle = %t, want %t", sample.seconds, green, sample.box)
		}
		if green && (minX < 28 || minX > 35 || minY < 14 || minY > 21 ||
			maxX < 93 || maxX > 100 || maxY < 51 || maxY > 58) {
			t.Errorf("at %ss rectangle bounds = %d,%d to %d,%d", sample.seconds, minX, minY, maxX, maxY)
		}
	}
	audio := exec.CommandContext(t.Context(), ffmpeg, "-hide_banner", "-loglevel", "error",
		"-i", outputPath, "-map", "0:a:0", "-f", "null", "-")
	if output, err := audio.CombinedOutput(); err != nil {
		t.Fatalf("decode retained audio: %v: %s", err, output)
	}
	original := httptest.NewRecorder()
	manager.Handler().ServeHTTP(original, httptest.NewRequest(http.MethodGet, path, nil))
	if original.Code != http.StatusOK || !bytes.Equal(original.Body.Bytes(), data) {
		t.Fatal("original download changed")
	}
	if original.Header().Get("ETag") == result.Header().Get("ETag") {
		t.Fatal("overlay and original have the same ETag")
	}
	for _, entry := range mustReadDir(t, dir) {
		if strings.HasPrefix(entry.Name(), ".netvue-overlay-") || strings.HasPrefix(entry.Name(), ".netvue-playback-") {
			t.Fatalf("temporary file remains: %s", entry.Name())
		}
	}
}

func TestRecordingOverlayFailureRemovesTemporaryFiles(t *testing.T) {
	dir := t.TempDir()
	store, err := newAIMetadataStoreWithLimits(dir, 10, time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	input, err := os.Create(filepath.Join(dir, "source.mp4"))
	if err != nil {
		t.Fatal(err)
	}
	defer input.Close()
	manager := &Manager{
		aiMetadata: store,
		recordings: &recordingManager{dir: dir},
		ffmpegPath: func() (string, error) { return filepath.Join(dir, "missing-ffmpeg"), nil },
	}
	_, _, err = manager.renderRecordingOverlay(t.Context(), input,
		RecordingSegment{Start: time.Now(), Duration: 1}, "camera")
	if err == nil {
		t.Fatal("missing ffmpeg accepted")
	}
	for _, entry := range mustReadDir(t, dir) {
		if strings.HasPrefix(entry.Name(), ".netvue-overlay-") {
			t.Fatalf("temporary file remains after failure: %s", entry.Name())
		}
	}
}

func mustReadDir(t *testing.T, dir string) []os.DirEntry {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	return entries
}
