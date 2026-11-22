package manager

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func settingsRequest(t *testing.T, handler http.Handler, method, path, body string, status int) string {
	t.Helper()
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest(method, path, strings.NewReader(body)))
	if response.Code != status {
		t.Fatalf("%s %s: got status %d, want %d: %s", method, path, response.Code, status, response.Body.String())
	}
	return response.Body.String()
}

func TestRuntimeRecordingSettingsPersistAndCleanWithoutDeletingProtected(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("NETVUE_RECORDINGS_DIR", dir)
	t.Setenv("NETVUE_PROTECTED_RECORDINGS_DIR", "")
	t.Setenv("NETVUE_RECORDING_RETENTION", "168h")
	t.Setenv("NETVUE_RECORDING_LIMIT_BYTES", "0")
	t.Setenv("NETVUE_CLOUDFLARE_TURN_KEY_ID", "")
	t.Setenv("NETVUE_CLOUDFLARE_TURN_API_TOKEN", "")
	manager := newTestManager(t, func(context.Context, string, string, string) error { return nil })
	handler := manager.Handler()

	old := filepath.Join(dir, "old.mp4")
	protected := filepath.Join(dir, "protected", "keep.mp4")
	for _, path := range []string{old, protected} {
		if err := os.WriteFile(path, []byte("video"), 0o600); err != nil {
			t.Fatal(err)
		}
		past := time.Now().Add(-2 * time.Hour)
		if err := os.Chtimes(path, past, past); err != nil {
			t.Fatal(err)
		}
	}
	settingsRequest(t, handler, "PUT", "/api/v1/settings",
		`{"recording_retention":"1h","recording_limit_bytes":1}`, 200)
	if _, err := os.Stat(old); !os.IsNotExist(err) {
		t.Fatalf("expired segment still exists: %v", err)
	}
	if _, err := os.Stat(protected); err != nil {
		t.Fatalf("protected file removed: %v", err)
	}
	settingsRequest(t, handler, "PUT", "/api/v1/settings", `{"recording_retention":"0s"}`, 400)
	settingsRequest(t, handler, "PUT", "/api/v1/settings", `{"recording_limit_bytes":-1}`, 400)
	t.Setenv("NETVUE_RECORDING_RETENTION", "999h")
	t.Setenv("NETVUE_RECORDING_LIMIT_BYTES", "999")
	reloaded := newTestManager(t, func(context.Context, string, string, string) error { return nil })
	body := settingsRequest(t, reloaded.Handler(), "GET", "/api/v1/settings", "", 200)
	var settings recordingSettings
	if err := json.Unmarshal([]byte(body), &settings); err != nil {
		t.Fatal(err)
	}
	if settings.Retention != "1h" || settings.LimitBytes != 1 {
		t.Fatalf("saved settings not preferred: %+v", settings)
	}
}

func TestRuntimeTURNConfigurationIsRedactedPersistentAndImmediate(t *testing.T) {
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	t.Setenv("NETVUE_CLOUDFLARE_TURN_KEY_ID", "env-key")
	t.Setenv("NETVUE_CLOUDFLARE_TURN_API_TOKEN", "env-token")
	manager := newTestManager(t, func(context.Context, string, string, string) error { return nil })
	handler := manager.Handler()
	requests := 0
	manager.turn.client.Transport = turnTestTransport(func(req *http.Request) (*http.Response, error) {
		requests++
		if req.Header.Get("Authorization") != "Bearer new-token" || !strings.Contains(req.URL.Path, "/new-key/") {
			t.Error("updated credentials not used")
		}
		body := `{"iceServers":[{"username":"user","credential":"secret","urls":["turn:turn.cloudflare.com:3478?transport=udp"]}]}`
		return &http.Response{StatusCode: http.StatusCreated, Body: io.NopCloser(strings.NewReader(body))}, nil
	})
	for _, body := range []string{
		settingsRequest(t, handler, "PUT", "/api/v1/webrtc/config", `{"enabled":true,"key_id":"new-key","api_token":"new-token"}`, 200),
		settingsRequest(t, handler, "GET", "/api/v1/webrtc/config", "", 200),
	} {
		if strings.Contains(body, "new-token") || strings.Contains(body, "api_token") {
			t.Fatal("API token exposed")
		}
	}
	settingsRequest(t, handler, "GET", "/api/v1/webrtc/ice", "", 200)
	if requests != 1 {
		t.Fatalf("TURN requests = %d", requests)
	}
	settingsRequest(t, handler, "PUT", "/api/v1/webrtc/config", `{"enabled":false}`, 200)
	settingsRequest(t, handler, "GET", "/api/v1/webrtc/ice", "", 204)
	settingsRequest(t, handler, "PUT", "/api/v1/webrtc/config", `{"enabled":true}`, 200)
	settingsRequest(t, handler, "GET", "/api/v1/webrtc/ice", "", 200)
	if requests != 2 {
		t.Fatal("TURN cache survived configuration change")
	}
	settingsRequest(t, handler, "PUT", "/api/v1/webrtc/config", `{"api_token":""}`, 400)
	reloaded := newTestManager(t, func(context.Context, string, string, string) error { return nil })
	body := settingsRequest(t, reloaded.Handler(), "GET", "/api/v1/webrtc/config", "", 200)
	if !strings.Contains(body, `"key_id":"new-key"`) || !strings.Contains(body, `"token_configured":true`) ||
		strings.Contains(body, "new-token") {
		t.Fatalf("unexpected restored config: %s", body)
	}
}

func TestRuntimeSettingsRejectInvalidRequestsWithoutChangingState(t *testing.T) {
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	t.Setenv("NETVUE_CLOUDFLARE_TURN_KEY_ID", "")
	t.Setenv("NETVUE_CLOUDFLARE_TURN_API_TOKEN", "")
	manager := newTestManager(t, func(context.Context, string, string, string) error { return nil })
	handler := manager.Handler()
	for _, body := range []string{`{}`, `{"enabled":true}`, `{"key_id":"bad/key"}`, `{"enabled":false} {}`, `{"unknown":1}`} {
		settingsRequest(t, handler, "PUT", "/api/v1/webrtc/config", body, 400)
	}
	settingsRequest(t, handler, "GET", "/api/v1/webrtc/ice", "", 204)
}

func TestRuntimeSizeLimitDeletesOldestWithoutTouchingProtected(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("NETVUE_RECORDINGS_DIR", dir)
	t.Setenv("NETVUE_PROTECTED_RECORDINGS_DIR", "")
	t.Setenv("NETVUE_RECORDING_RETENTION", "168h")
	t.Setenv("NETVUE_RECORDING_LIMIT_BYTES", "0")
	t.Setenv("NETVUE_CLOUDFLARE_TURN_KEY_ID", "")
	t.Setenv("NETVUE_CLOUDFLARE_TURN_API_TOKEN", "")
	manager := newTestManager(t, func(context.Context, string, string, string) error { return nil })
	old := filepath.Join(dir, "old.mp4")
	newer := filepath.Join(dir, "new.mp4")
	protected := filepath.Join(dir, "protected", "keep.mp4")
	for _, path := range []string{old, newer, protected} {
		if err := os.WriteFile(path, []byte("12345"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	past := time.Now().Add(-time.Hour)
	if err := os.Chtimes(old, past, past); err != nil {
		t.Fatal(err)
	}
	settingsRequest(t, manager.Handler(), "PUT", "/api/v1/settings", `{"recording_limit_bytes":5}`, 200)
	if _, err := os.Stat(old); !os.IsNotExist(err) {
		t.Fatalf("oldest segment still exists: %v", err)
	}
	for _, path := range []string{newer, protected} {
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("file %s removed: %v", path, err)
		}
	}
}

func TestRuntimeSettingsFailedPersistenceDoesNotChangeConfiguration(t *testing.T) {
	t.Setenv("NETVUE_RECORDINGS_DIR", "")
	t.Setenv("NETVUE_CLOUDFLARE_TURN_KEY_ID", "")
	t.Setenv("NETVUE_CLOUDFLARE_TURN_API_TOKEN", "")
	manager := newTestManager(t, func(context.Context, string, string, string) error { return nil })
	handler := manager.Handler()
	settingsRequest(t, handler, "PUT", "/api/v1/webrtc/config",
		`{"enabled":true,"key_id":"key","api_token":"token"}`, 500)
	settingsRequest(t, handler, "GET", "/api/v1/webrtc/ice", "", 204)
	settingsRequest(t, handler, "PUT", "/api/v1/settings", `{"recording_retention":"1h"}`, 500)
	body := settingsRequest(t, handler, "GET", "/api/v1/settings", "", 200)
	if strings.Contains(body, `"recording_retention":"1h"`) {
		t.Fatal("unpersisted retention became active")
	}
}

func TestRuntimeTURNConcurrentRequests(t *testing.T) {
	t.Setenv("NETVUE_RECORDINGS_DIR", t.TempDir())
	t.Setenv("NETVUE_CLOUDFLARE_TURN_KEY_ID", "")
	t.Setenv("NETVUE_CLOUDFLARE_TURN_API_TOKEN", "")
	manager := newTestManager(t, func(context.Context, string, string, string) error { return nil })
	manager.turn.client.Transport = turnTestTransport(func(*http.Request) (*http.Response, error) {
		body := `{"iceServers":[{"username":"user","credential":"secret","urls":["turn:turn.cloudflare.com:3478?transport=udp"]}]}`
		return &http.Response{StatusCode: http.StatusCreated, Body: io.NopCloser(strings.NewReader(body))}, nil
	})
	handler := manager.Handler()
	var wg sync.WaitGroup
	for range 3 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for range 10 {
				response := httptest.NewRecorder()
				handler.ServeHTTP(response, httptest.NewRequest("PUT", "/api/v1/webrtc/config",
					strings.NewReader(`{"enabled":true,"key_id":"key","api_token":"token"}`)))
				if response.Code != http.StatusOK {
					t.Errorf("PUT config: %d", response.Code)
				}
				response = httptest.NewRecorder()
				handler.ServeHTTP(response, httptest.NewRequest("GET", "/api/v1/webrtc/ice", nil))
				if response.Code != http.StatusOK {
					t.Errorf("GET ice: %d", response.Code)
				}
			}
		}()
	}
	wg.Wait()
}
