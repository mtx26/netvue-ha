package manager

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"image/jpeg"
	"io"
	"math"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestRecordingManagerManualContinuousAndList(t *testing.T) {
	var mode bool
	var deleted bool
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/v3/config/paths/get/camera":
			w.WriteHeader(http.StatusNotFound)
		case r.Method == http.MethodPost && r.URL.Path == "/v3/config/paths/add/camera":
			var payload map[string]any
			if json.NewDecoder(r.Body).Decode(&payload) == nil {
				mode, _ = payload["record"].(bool)
			}
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(`{"status":"ok"}`))
		case r.Method == http.MethodGet && r.URL.Path == "/v3/recordings/get/camera":
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(`{"name":"camera","segments":[{"start":"2026-11-20T12:00:00Z","duration":60}]}`))
		case r.Method == http.MethodDelete:
			deleted = true
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(`{"status":"ok"}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer control.Close()

	dir := t.TempDir()
	t.Setenv("NETVUE_RECORDINGS_DIR", dir)
	manager := newRecordingManager(context.Background())
	manager.controlURL = control.URL

	status, err := manager.setMode("camera", "manual", true)
	if err != nil {
		t.Fatal(err)
	}
	if !status.Manual || !mode {
		t.Fatalf("manual status = %+v, MediaMTX mode %v", status, mode)
	}
	segments, err := manager.list("camera", time.Time{}, time.Time{})
	if err != nil {
		t.Fatal(err)
	}
	if len(segments) != 1 {
		t.Fatalf("segments = %+v", segments)
	}
	if err := manager.deleteSegment("camera", segments[0].ID); err != nil {
		t.Fatal(err)
	}
	if !deleted {
		t.Fatal("delete segment was not sent to MediaMTX")
	}

	status, err = manager.setMode("camera", "continuous", true)
	if err != nil {
		t.Fatal(err)
	}
	if !status.Continuous {
		t.Fatalf("continuous status = %+v", status)
	}
	if data, err := os.ReadFile(filepath.Join(dir, ".netvue-recordings.json")); err != nil || len(data) == 0 {
		t.Fatalf("persistent settings missing: data=%q err=%v", data, err)
	}
}

func TestRecordingCanBeResolvedAfterSegmentDurationChanges(t *testing.T) {
	var calls int
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		duration := 10
		if calls > 1 {
			duration = 20
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"name": "camera",
			"segments": []map[string]any{{
				"start": "2026-11-20T12:00:00Z", "duration": duration,
			}},
		})
	}))
	defer control.Close()
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	manager := newRecordingManager(t.Context())
	manager.controlURL = control.URL

	segments, err := manager.list("camera", time.Time{}, time.Time{})
	if err != nil || len(segments) != 1 {
		t.Fatalf("list = %+v, %v", segments, err)
	}
	segment, found, err := manager.findSegment("camera", segments[0].ID)
	if err != nil || !found || segment.Duration != 20 {
		t.Fatalf("growing segment = %+v, found %t, error %v", segment, found, err)
	}
}

func TestRecordingDownloadSupportsRangesAndReportsSize(t *testing.T) {
	const body = "synthetic recording contents"
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v3/recordings/get/camera" {
			http.NotFound(w, r)
			return
		}
		_, _ = io.WriteString(w, `{"segments":[{"start":"2026-11-20T12:00:00Z","duration":60}]}`)
	}))
	defer control.Close()
	playback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Accept-Ranges", "none")
		w.(http.Flusher).Flush()
		_, _ = io.WriteString(w, body)
	}))
	defer playback.Close()
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	manager, err := newManager(t.Context(), "192.168.10.1", func(context.Context, string, string, string) error {
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	manager.recordings.controlURL = control.URL
	manager.recordings.playbackAddress = playback.URL
	manager.cameras["192.168.10.2"] = &cameraSession{Camera: Camera{
		Host: "192.168.10.2", StreamName: "camera",
	}}
	start, err := time.Parse(time.RFC3339, "2026-11-20T12:00:00Z")
	if err != nil {
		t.Fatal(err)
	}
	path := "/api/v1/cameras/192.168.10.2/recordings/" + recordingID("camera", start) + "/download"
	handler := manager.Handler()
	request := httptest.NewRequest(http.MethodGet, path, nil)
	full := httptest.NewRecorder()
	handler.ServeHTTP(full, request)
	if full.Code != http.StatusOK || full.Body.String() != body ||
		full.Header().Get("Content-Length") != "28" || full.Header().Get("Accept-Ranges") != "bytes" {
		t.Fatalf("full response = %d, headers %v, body %q", full.Code, full.Header(), full.Body.String())
	}
	request = httptest.NewRequest(http.MethodGet, path, nil)
	request.Header.Set("Range", "bytes=4-8")
	part := httptest.NewRecorder()
	handler.ServeHTTP(part, request)
	if part.Code != http.StatusPartialContent || part.Body.String() != body[4:9] ||
		part.Header().Get("Content-Range") != "bytes 4-8/28" {
		t.Fatalf("range response = %d, headers %v, body %q", part.Code, part.Header(), part.Body.String())
	}
	request = httptest.NewRequest(http.MethodGet, path, nil)
	request.Header.Set("Range", "bytes=999-")
	invalid := httptest.NewRecorder()
	handler.ServeHTTP(invalid, request)
	if invalid.Code != http.StatusRequestedRangeNotSatisfiable ||
		invalid.Header().Get("Content-Range") != "bytes */28" {
		t.Fatalf("invalid range response = %d, headers %v", invalid.Code, invalid.Header())
	}
	request = httptest.NewRequest(http.MethodHead, path, nil)
	head := httptest.NewRecorder()
	handler.ServeHTTP(head, request)
	if head.Code != http.StatusOK || head.Header().Get("Content-Length") != "28" || head.Body.Len() != 0 {
		t.Fatalf("HEAD response = %d, headers %v, body %q", head.Code, head.Header(), head.Body.String())
	}
	for _, invalidDuration := range []string{"-1", "NaN", "61"} {
		result := httptest.NewRecorder()
		handler.ServeHTTP(result, httptest.NewRequest(http.MethodGet, path+"?duration="+invalidDuration, nil))
		if result.Code != http.StatusBadRequest {
			t.Fatalf("duration %q: response %d", invalidDuration, result.Code)
		}
	}
	entries, err := os.ReadDir(manager.recordings.dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".netvue-playback-") {
			t.Fatalf("temporary playback file remains: %s", entry.Name())
		}
	}
}

func TestRecordingPlaybackWithMediaMTXAndFFmpeg(t *testing.T) {
	ffmpeg, mediamtx := os.Getenv("NETVUE_TEST_FFMPEG"), os.Getenv("NETVUE_TEST_MEDIAMTX")
	if ffmpeg == "" || mediamtx == "" {
		t.Skip("set NETVUE_TEST_FFMPEG and NETVUE_TEST_MEDIAMTX for the synthetic playback test")
	}
	dir := t.TempDir()
	recordDir := filepath.Join(dir, "camera")
	if err := os.MkdirAll(recordDir, 0o750); err != nil {
		t.Fatal(err)
	}
	recording := filepath.Join(recordDir, "2026-11-20_12-00-00-000000.mp4")
	cmd := exec.CommandContext(t.Context(), ffmpeg, "-hide_banner", "-loglevel", "error",
		"-f", "lavfi", "-i", "testsrc=size=320x180:rate=5", "-t", "4", "-c:v", "libx264",
		"-pix_fmt", "yuv420p", "-g", "5", "-movflags", "empty_moov+frag_keyframe+default_base_moof",
		"-f", "mp4", recording)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("create synthetic recording: %v: %s", err, output)
	}
	data, err := os.ReadFile(recording)
	if err != nil {
		t.Fatal(err)
	}
	firstPartsEnd, parts := 0, 0
	for offset := 0; offset+8 <= len(data); {
		size := int(binary.BigEndian.Uint32(data[offset : offset+4]))
		if size < 8 || offset+size > len(data) {
			t.Fatal("invalid synthetic MP4 box")
		}
		if string(data[offset+4:offset+8]) == "mdat" {
			parts++
			if parts == 2 {
				firstPartsEnd = offset + size
				break
			}
		}
		offset += size
	}
	if firstPartsEnd == 0 || firstPartsEnd == len(data) {
		t.Fatal("synthetic recording does not contain multiple fragments")
	}
	writer, err := os.OpenFile(recording, os.O_WRONLY|os.O_TRUNC, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if _, err := writer.Write(data[:firstPartsEnd]); err != nil {
		t.Fatal(err)
	}
	freeAddress := func() string {
		t.Helper()
		listener, err := net.Listen("tcp4", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		address := listener.Addr().String()
		_ = listener.Close()
		return address
	}
	apiAddress, playbackAddress := freeAddress(), freeAddress()
	config := fmt.Sprintf(`logLevel: error
rtsp: false
rtmp: false
hls: false
webrtc: false
srt: false
moq: false
api: true
apiAddress: %s
playback: true
playbackAddress: %s
pathDefaults:
  record: true
  recordFormat: fmp4
  recordPath: '%s/%%path/%%Y-%%m-%%d_%%H-%%M-%%S-%%f'
paths:
  all_others:
`, apiAddress, playbackAddress, filepath.ToSlash(dir))
	configPath := filepath.Join(dir, "mediamtx.yml")
	if err := os.WriteFile(configPath, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	server := exec.CommandContext(t.Context(), mediamtx, configPath)
	var logs bytes.Buffer
	server.Stdout, server.Stderr = &logs, &logs
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = server.Process.Kill()
		_ = server.Wait()
		if t.Failed() {
			t.Logf("MediaMTX output: %s", logs.String())
		}
	})
	ctx, cancel := context.WithTimeout(t.Context(), 15*time.Second)
	defer cancel()
	for {
		var dialer net.Dialer
		conn, err := dialer.DialContext(ctx, "tcp4", playbackAddress)
		if err == nil {
			_ = conn.Close()
			break
		}
		select {
		case <-ctx.Done():
			t.Fatalf("MediaMTX did not start: %v", err)
		case <-time.After(50 * time.Millisecond):
		}
	}
	t.Setenv("NETVUE_RECORDINGS_DIR", dir)
	manager, err := newManager(t.Context(), "192.168.10.1", func(context.Context, string, string, string) error {
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	manager.recordings.controlURL = "http://" + apiAddress
	manager.recordings.playbackAddress = "http://" + playbackAddress
	manager.ffmpegPath = func() (string, error) { return ffmpeg, nil }
	manager.cameras["192.168.10.2"] = &cameraSession{Camera: Camera{
		Host: "192.168.10.2", StreamName: "camera",
	}}
	segments, err := manager.recordings.list("camera", time.Time{}, time.Time{})
	if err != nil || len(segments) != 1 || segments[0].Duration <= 0 {
		t.Fatalf("synthetic recordings = %+v, error %v", segments, err)
	}
	path := "/api/v1/cameras/192.168.10.2/recordings/" + segments[0].ID
	duration := math.Floor(segments[0].Duration*1000) / 1000
	snapshotPath := path + "/download?duration=" + strconv.FormatFloat(duration, 'f', 3, 64)
	full := httptest.NewRecorder()
	manager.Handler().ServeHTTP(full, httptest.NewRequest(http.MethodGet, snapshotPath, nil))
	if full.Code != http.StatusOK || full.Body.Len() == 0 ||
		full.Header().Get("Content-Length") != fmt.Sprint(full.Body.Len()) {
		t.Fatalf("playback = %d, headers %v, body length %d", full.Code, full.Header(), full.Body.Len())
	}
	request := httptest.NewRequest(http.MethodGet, snapshotPath, nil)
	request.Header.Set("Range", "bytes=10-29")
	part := httptest.NewRecorder()
	manager.Handler().ServeHTTP(part, request)
	if part.Code != http.StatusPartialContent || !bytes.Equal(part.Body.Bytes(), full.Body.Bytes()[10:30]) {
		t.Fatalf("partial playback = %d, headers %v, body %q", part.Code, part.Header(), part.Body.Bytes())
	}
	thumb := httptest.NewRecorder()
	manager.Handler().ServeHTTP(thumb, httptest.NewRequest(http.MethodGet, path+"/thumbnail?offset=0", nil))
	image, err := jpeg.DecodeConfig(bytes.NewReader(thumb.Body.Bytes()))
	if thumb.Code != http.StatusOK || err != nil || image.Width > 320 || image.Height > 180 {
		t.Fatalf("thumbnail = %d, image %+v, error %v, body %q", thumb.Code, image, err, thumb.Body.Bytes())
	}
	invalidThumb := httptest.NewRecorder()
	manager.Handler().ServeHTTP(invalidThumb, httptest.NewRequest(http.MethodGet, path+"/thumbnail?offset=-1", nil))
	if invalidThumb.Code != http.StatusBadRequest {
		t.Fatalf("invalid thumbnail offset = %d", invalidThumb.Code)
	}
	if _, err := writer.Write(data[firstPartsEnd:]); err != nil {
		t.Fatal(err)
	}
	grown, err := manager.recordings.list("camera", time.Time{}, time.Time{})
	if err != nil || len(grown) != 1 || grown[0].ID != segments[0].ID || grown[0].Duration <= segments[0].Duration {
		t.Fatalf("growing recording = %+v, previous %+v, error %v", grown, segments, err)
	}
	laterThumb := httptest.NewRecorder()
	manager.Handler().ServeHTTP(laterThumb, httptest.NewRequest(http.MethodGet, path+"/thumbnail?offset=1", nil))
	if _, err := jpeg.DecodeConfig(bytes.NewReader(laterThumb.Body.Bytes())); laterThumb.Code != http.StatusOK || err != nil {
		t.Fatalf("later thumbnail = %d, error %v, body %q, segment %+v",
			laterThumb.Code, err, laterThumb.Body.Bytes(), grown[0])
	}
	if bytes.Equal(laterThumb.Body.Bytes(), thumb.Body.Bytes()) {
		t.Fatal("thumbnail did not change at a later time")
	}
	snapshot := httptest.NewRecorder()
	manager.Handler().ServeHTTP(snapshot, httptest.NewRequest(http.MethodGet, snapshotPath, nil))
	if snapshot.Code != http.StatusOK || !bytes.Equal(snapshot.Body.Bytes(), full.Body.Bytes()) {
		t.Fatalf("snapshot changed after recording grew: %d, old length %d, new length %d",
			snapshot.Code, full.Body.Len(), snapshot.Body.Len())
	}
	request = httptest.NewRequest(http.MethodGet, path+"/download", nil)
	request.Header.Set("Range", "bytes=10-29")
	request.Header.Set("If-Range", full.Header().Get("ETag"))
	changed := httptest.NewRecorder()
	manager.Handler().ServeHTTP(changed, request)
	if changed.Code != http.StatusOK || changed.Body.Len() <= full.Body.Len() {
		t.Fatalf("growing playback = %d, headers %v, body length %d", changed.Code, changed.Header(), changed.Body.Len())
	}
	decode := exec.CommandContext(t.Context(), ffmpeg, "-hide_banner", "-loglevel", "error",
		"-i", "pipe:0", "-frames:v", "1", "-f", "null", "-")
	decode.Stdin = bytes.NewReader(changed.Body.Bytes())
	if output, err := decode.CombinedOutput(); err != nil {
		t.Fatalf("decode exported recording: %v: %s", err, output)
	}
}

func TestRecordingCleanupDeletesOnlyExpiredSegmentFiles(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("NETVUE_RECORDINGS_DIR", dir)
	t.Setenv("NETVUE_RECORDING_RETENTION", "1h")
	t.Setenv("NETVUE_RECORDING_LIMIT_BYTES", "0")

	oldSegment := filepath.Join(dir, "camera", "old.mp4")
	unrelated := filepath.Join(dir, "camera", "keep.txt")
	if err := os.MkdirAll(filepath.Dir(oldSegment), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(oldSegment, []byte("old"), 0o640); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(unrelated, []byte("keep"), 0o640); err != nil {
		t.Fatal(err)
	}
	oldTime := time.Now().Add(-2 * time.Hour)
	if err := os.Chtimes(oldSegment, oldTime, oldTime); err != nil {
		t.Fatal(err)
	}

	manager := newRecordingManager(context.Background())
	manager.cleanup()
	if _, err := os.Stat(oldSegment); !os.IsNotExist(err) {
		t.Fatalf("expired segment still exists: %v", err)
	}
	if _, err := os.Stat(unrelated); err != nil {
		t.Fatalf("unrelated file removed: %v", err)
	}
}
