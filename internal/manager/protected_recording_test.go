package manager

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
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

func TestProtectedCoverageReportsEveryGap(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"segments":[
			{"start":"2026-01-01T00:00:02Z","duration":3},
			{"start":"2026-01-01T00:00:04Z","duration":3},
			{"start":"2026-01-01T00:00:09Z","duration":2}
		]}`)
	}))
	t.Cleanup(server.Close)
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	manager := newRecordingManager(t.Context())
	manager.controlURL = server.URL
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	segments, gaps, err := manager.protectionCoverage("camera", start, start.Add(12*time.Second))
	if err != nil || len(segments) != 3 || len(gaps) != 3 {
		t.Fatalf("segments=%+v gaps=%+v err=%v", segments, gaps, err)
	}
	for i, expected := range [][2]int{{0, 2}, {7, 9}, {11, 12}} {
		if !gaps[i].Start.Equal(start.Add(time.Duration(expected[0])*time.Second)) ||
			!gaps[i].End.Equal(start.Add(time.Duration(expected[1])*time.Second)) {
			t.Fatalf("gap %d = %+v", i, gaps[i])
		}
	}
}

func TestProtectedCleanupDoesNotRemoveProtectedFiles(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("NETVUE_RECORDINGS_DIR", dir)
	t.Setenv("NETVUE_PROTECTED_RECORDINGS_DIR", filepath.Join(dir, "protected"))
	t.Setenv("NETVUE_RECORDING_RETENTION", "1h")
	t.Setenv("NETVUE_RECORDING_LIMIT_BYTES", "1")
	manager := newRecordingManager(t.Context())
	path := filepath.Join(manager.protectedDir, "keep.mp4")
	if err := os.WriteFile(path, []byte("protected"), 0o600); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-2 * time.Hour)
	if err := os.Chtimes(path, old, old); err != nil {
		t.Fatal(err)
	}
	manager.cleanup()
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("protected file removed: %v", err)
	}
}

func TestProtectedFailureIsIndexedAndGapsReturned(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"segments":[]}`)
	}))
	t.Cleanup(server.Close)
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	manager := newRecordingManager(t.Context())
	manager.controlURL = server.URL
	start := time.Now().UTC()
	result, err := manager.protectRange(t.Context(), "camera", "host", "", "", "manual", start, start.Add(time.Minute))
	if err == nil || result.Status != "failed" || result.ID == "" || len(result.Gaps) != 1 {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	index, err := manager.protectedRecordings()
	if err != nil || len(index) != 1 || index[0].ID != result.ID || index[0].Status != "failed" {
		t.Fatalf("index=%+v err=%v", index, err)
	}
	if err := manager.deleteProtectedRecording(result.ID); err != nil {
		t.Fatal(err)
	}
}

func TestProtectedStorageHealthEnforcesThreshold(t *testing.T) {
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	manager := newRecordingManager(t.Context())
	t.Setenv("NETVUE_PROTECTED_MIN_FREE_BYTES", "18446744073709551615")
	health, err := manager.protectedHealth()
	if err == nil || health.Healthy {
		t.Fatalf("health=%+v err=%v", health, err)
	}
	t.Setenv("NETVUE_PROTECTED_MIN_FREE_BYTES", "invalid")
	if _, err := manager.protectedHealth(); err == nil {
		t.Fatal("invalid threshold accepted")
	}
}

func TestProtectedIndexRecoversInterruptedPublication(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("NETVUE_RECORDINGS_DIR", dir)
	manager := newRecordingManager(t.Context())
	items := []ProtectedRecording{
		{ID: "published", Filename: "published.mp4", Status: "saving", Size: 5},
		{ID: "unfinished", Filename: "unfinished.mp4", Status: "saving", Size: 5},
	}
	if err := os.WriteFile(filepath.Join(manager.protectedDir, "published.mp4"), []byte("video"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := manager.saveProtectedIndex(items); err != nil {
		t.Fatal(err)
	}
	reloaded := newRecordingManager(t.Context())
	items, err := reloaded.protectedRecordings()
	if err != nil || len(items) != 2 || items[0].Status != "saved" || items[1].Status != "failed" {
		t.Fatalf("recovered = %+v, error %v", items, err)
	}
}

func TestProtectedExportRejectsConcurrentWork(t *testing.T) {
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	manager := newRecordingManager(t.Context())
	manager.protectedSlot <- struct{}{}
	t.Cleanup(func() { <-manager.protectedSlot })
	start := time.Now()
	_, err := manager.protectRange(t.Context(), "camera", "host", "", "", "manual", start, start.Add(time.Minute))
	if err == nil || !strings.Contains(err.Error(), "busy") {
		t.Fatalf("concurrent export: %v", err)
	}
}

func TestProtectedFFmpegHonorsCancellation(t *testing.T) {
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	t.Setenv("NETVUE_PROTECTED_MIN_FREE_BYTES", "0")
	manager := newRecordingManager(t.Context())
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	if err := manager.runProtectedFFmpeg(ctx, executable); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled export: %v", err)
	}
}

func TestProtectedRoutesReportGapsAndHealth(t *testing.T) {
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"segments":[]}`)
	}))
	t.Cleanup(control.Close)
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	t.Setenv("NETVUE_PROTECTED_MIN_FREE_BYTES", "0")
	manager, err := newManager(t.Context(), "192.168.10.1", func(context.Context, string, string, string) error {
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	manager.cameras["192.168.10.2"] = &cameraSession{Camera: Camera{
		Host: "192.168.10.2", StreamName: "camera",
	}}
	manager.recordings.controlURL = control.URL
	handler := manager.Handler()
	result := httptest.NewRecorder()
	handler.ServeHTTP(result, httptest.NewRequest(http.MethodPost, "/api/v1/cameras/192.168.10.2/protected",
		strings.NewReader(`{"start":"2026-01-01T00:00:00Z","end":"2026-01-01T00:00:10Z"}`)))
	var response struct {
		Gaps      []RecordingRange   `json:"gaps"`
		Recording ProtectedRecording `json:"recording"`
	}
	if err := json.Unmarshal(result.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if result.Code != http.StatusConflict || len(response.Gaps) != 1 || response.Recording.Status != "failed" {
		t.Fatalf("gap response = %d, %s", result.Code, result.Body.String())
	}
	result = httptest.NewRecorder()
	handler.ServeHTTP(result, httptest.NewRequest(http.MethodGet, "/api/v1/recordings/protected/health", nil))
	if result.Code != http.StatusOK {
		t.Fatalf("health response = %d, %s", result.Code, result.Body.String())
	}
	t.Setenv("NETVUE_PROTECTED_MIN_FREE_BYTES", "18446744073709551615")
	result = httptest.NewRecorder()
	handler.ServeHTTP(result, httptest.NewRequest(http.MethodGet, "/api/v1/recordings/protected/health", nil))
	if result.Code != http.StatusServiceUnavailable {
		t.Fatalf("unhealthy response = %d, %s", result.Code, result.Body.String())
	}
	t.Setenv("NETVUE_PROTECTED_MIN_FREE_BYTES", "0")
	if err := manager.recordings.saveProtectedIndex([]ProtectedRecording{{
		ID: "missing", Filename: "missing.mp4", Status: "saved", Size: 42,
	}}); err != nil {
		t.Fatal(err)
	}
	result = httptest.NewRecorder()
	handler.ServeHTTP(result, httptest.NewRequest(http.MethodGet, "/api/v1/recordings/protected/health", nil))
	if result.Code != http.StatusServiceUnavailable {
		t.Fatalf("missing file health response = %d, %s", result.Code, result.Body.String())
	}
}

func TestProtectedExportAcrossSegments(t *testing.T) {
	ffmpeg := os.Getenv("NETVUE_TEST_FFMPEG")
	if ffmpeg == "" {
		t.Skip("set NETVUE_TEST_FFMPEG for synthetic multi-segment export")
	}

	testMediaMTX := func(t *testing.T) {
		ffmpeg, mediamtx := os.Getenv("NETVUE_TEST_FFMPEG"), os.Getenv("NETVUE_TEST_MEDIAMTX")
		if ffmpeg == "" || mediamtx == "" {
			t.Skip("set NETVUE_TEST_FFMPEG and NETVUE_TEST_MEDIAMTX for synthetic MediaMTX export")
		}
		t.Setenv("PATH", filepath.Dir(ffmpeg)+string(os.PathListSeparator)+os.Getenv("PATH"))
		dir := t.TempDir()
		t.Setenv("NETVUE_RECORDINGS_DIR", dir)
		t.Setenv("NETVUE_PROTECTED_MIN_FREE_BYTES", "0")
		if err := os.Mkdir(filepath.Join(dir, "camera"), 0o750); err != nil {
			t.Fatal(err)
		}
		for i, spec := range []struct {
			color string
			name  string
		}{
			{color: "red", name: "2026-01-01_00-00-00-000000.mp4"},
			{color: "blue", name: "2026-01-01_00-00-02-000000.mp4"},
		} {
			path := filepath.Join(dir, "camera", spec.name)
			cmd := exec.CommandContext(t.Context(), ffmpeg, "-hide_banner", "-loglevel", "error",
				"-f", "lavfi", "-i", "color="+spec.color+":size=64x64:rate=5", "-t", "3",
				"-c:v", "libx264", "-g", "5", "-bf", "0", "-pix_fmt", "yuv420p",
				"-video_track_timescale", "90000",
				"-movflags", "empty_moov+frag_keyframe+default_base_moof", path)
			if output, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("generate segment %d: %v: %s", i, err, output)
			}
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
	  recordDeleteAfter: 0s
	paths:
	  all_others:
	`, apiAddress, playbackAddress, filepath.ToSlash(dir))
		configPath := filepath.Join(dir, "mediamtx.yml")
		if err := os.WriteFile(configPath, []byte(strings.ReplaceAll(config, "\t", "")), 0o600); err != nil {
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
		manager := newRecordingManager(t.Context())
		manager.controlURL = "http://" + apiAddress
		manager.playbackAddress = "http://" + playbackAddress
		segments, err := manager.list("camera", time.Time{}, time.Time{})
		if err != nil || len(segments) != 2 {
			t.Fatalf("segments = %+v, error %v", segments, err)
		}
		start := segments[0].Start.Add(time.Second)
		end := segments[1].Start.Add(time.Second)
		result, err := manager.protectRange(t.Context(), "camera", "host", "", "", "manual", start, end)
		if err == nil || result.Status != "failed" || !strings.Contains(err.Error(), "timestamps") {
			t.Fatalf("invalid timestamps accepted: export = %+v, error %v", result, err)
		}
	}
	t.Run("MediaMTXRejectsDuplicateTimestamps", testMediaMTX)
	t.Setenv("PATH", filepath.Dir(ffmpeg)+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	t.Setenv("NETVUE_PROTECTED_MIN_FREE_BYTES", "0")
	files := make([][]byte, 2)
	for i, color := range []string{"red", "blue"} {
		path := filepath.Join(t.TempDir(), color+".mp4")
		cmd := exec.CommandContext(t.Context(), ffmpeg, "-hide_banner", "-loglevel", "error",
			"-f", "lavfi", "-i", "color="+color+":size=64x64:rate=5", "-t", "2",
			"-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p", path)
		if output, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("generate input: %v: %s", err, output)
		}
		var err error
		files[i], err = os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
	}
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	control := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"segments": []map[string]any{
			{"start": start, "duration": 2}, {"start": start.Add(2 * time.Second), "duration": 2},
		}})
	}))
	t.Cleanup(control.Close)
	playback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		i := 0
		if strings.Contains(r.URL.Query().Get("start"), "00:00:02") {
			i = 1
		}
		http.ServeContent(w, r, "source.mp4", time.Time{}, bytes.NewReader(files[i]))
	}))
	t.Cleanup(playback.Close)
	manager := newRecordingManager(t.Context())
	manager.controlURL, manager.playbackAddress = control.URL, playback.URL
	result, err := manager.protectRange(t.Context(), "camera", "host", "", "", "manual",
		start.Add(time.Second), start.Add(3*time.Second))
	if err != nil || result.Status != "saved" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
	path := filepath.Join(manager.protectedDir, result.Filename)
	cmd := exec.CommandContext(t.Context(), ffmpeg, "-hide_banner", "-loglevel", "error", "-i", path,
		"-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
	output, err := cmd.Output()
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	frameSize := 64 * 64 * 3
	if len(output) != 10*frameSize {
		t.Fatalf("decoded frames = %s, expected 10", strconv.FormatFloat(float64(len(output))/float64(frameSize), 'f', 2, 64))
	}
	first, last := output[:3], output[len(output)-frameSize:len(output)-frameSize+3]
	if first[0] < 200 || first[2] > 30 || last[2] < 200 || last[0] > 30 {
		t.Fatalf("wrong boundary frames: %s", fmt.Sprint(first, last))
	}
}
