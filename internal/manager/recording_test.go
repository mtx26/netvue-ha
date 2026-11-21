package manager

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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
