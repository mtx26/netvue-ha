package manager

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type turnTestTransport func(*http.Request) (*http.Response, error)

func (fn turnTestTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	return fn(req)
}

func TestCloudflareTURNNotFoundDiagnostic(t *testing.T) {
	for _, tt := range []struct {
		name, body, want string
	}{
		{"missing key", `{"error":"cannot find specified key"}`, "Cloudflare API HTTP 404: TURN key not found"},
		{"other upstream error", `{"error":"private upstream detail"}`, "Cloudflare API HTTP 404"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			turn := &cloudflareTURN{keyID: "test-id", token: "test-token", client: &http.Client{
				Transport: turnTestTransport(func(*http.Request) (*http.Response, error) {
					return &http.Response{StatusCode: http.StatusNotFound, Body: io.NopCloser(strings.NewReader(tt.body))}, nil
				}),
			}}
			servers, err := turn.generate(context.Background())
			if len(servers) != 0 || err == nil || !strings.HasPrefix(err.Error(), tt.want) {
				t.Fatalf("unexpected result: servers=%d, error=%v", len(servers), err)
			}
			if tt.name == "other upstream error" && err.Error() != tt.want {
				t.Fatal("unrecognized upstream content must not be logged")
			}
		})
	}
}

func TestCloudflareTURNLocalCredentials(t *testing.T) {
	t.Setenv("NETVUE_CLOUDFLARE_TURN_KEY_ID", "test-id")
	t.Setenv("NETVUE_CLOUDFLARE_TURN_API_TOKEN", "test-api-token")
	turn, err := cloudflareTURNFromEnv()
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	status := http.StatusCreated
	turn.client.Transport = turnTestTransport(func(req *http.Request) (*http.Response, error) {
		calls++
		var payload struct{ TTL int }
		if err := json.NewDecoder(req.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		if req.Method != "POST" || req.URL.String() != "https://rtc.live.cloudflare.com/v1/turn/keys/test-id/credentials/generate-ice-servers" ||
			req.Header.Get("Authorization") != "Bearer test-api-token" || payload.TTL != 3600 {
			t.Fatal("invalid Cloudflare credential request")
		}
		body := `{"iceServers":[{"urls":["stun:stun.cloudflare.com:3478"]},{"username":"temporary-user","credential":"temporary-secret","urls":["turn:turn.cloudflare.com:3478?transport=udp","turns:turn.cloudflare.com:443?transport=tcp","turn:turn.cloudflare.com:53?transport=udp"]}]}`
		return &http.Response{StatusCode: status, Body: io.NopCloser(strings.NewReader(body))}, nil
	})
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error { return nil })
	manager.turn = newCloudflareTURN(turnSettings{})
	handler := manager.Handler()
	const path = "/api/v1/webrtc/ice"
	if got := requestWebRTC(t, handler, "GET", path, nil); got.Code != 204 {
		t.Fatalf("disabled: status %d", got.Code)
	}
	manager.turn = turn
	for range 2 {
		got := requestWebRTC(t, handler, "GET", path, nil)
		body := got.Body.String()
		if got.Code != 200 || got.Header().Get("Cache-Control") != "no-store" ||
			!strings.Contains(body, "temporary-secret") || !strings.Contains(body, "turns:turn.cloudflare.com:443") ||
			strings.Contains(body, "test-api-token") || strings.Contains(body, "test-id") || strings.Contains(body, ":53?") {
			t.Fatalf("incorrect local ICE response: status %d", got.Code)
		}
	}
	if calls != 1 {
		t.Fatalf("cache: issued %d Cloudflare requests", calls)
	}
	turn.expires = time.Now().Add(12 * time.Minute)
	status = http.StatusUnauthorized
	if got := requestWebRTC(t, handler, "GET", path, nil); got.Code != 200 {
		t.Fatal("refresh error discarded still usable credentials")
	}
	turn.expires = time.Now().Add(-time.Minute)
	if got := requestWebRTC(t, handler, "GET", path, nil); got.Code != 503 || strings.Contains(got.Body.String(), "temporary-secret") {
		t.Fatal("expired credentials were exposed")
	}
	if calls != 2 {
		t.Fatal("failed requests were not throttled")
	}
}
