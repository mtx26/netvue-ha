package manager

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/pion/webrtc/v4"
)

const (
	cloudflareTURNTTL = time.Hour
	turnRefreshAhead  = 15 * time.Minute
	turnRetryDelay    = time.Minute
)

var cloudflareTURNKeyID = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,128}$`)

type cloudflareTURN struct {
	keyID, token string
	client       *http.Client
	mu           sync.Mutex
	servers      []webrtc.ICEServer
	expires      time.Time
	retry        time.Time
}

func cloudflareTURNFromEnv() (*cloudflareTURN, error) {
	keyID := strings.TrimSpace(os.Getenv("NETVUE_CLOUDFLARE_TURN_KEY_ID"))
	token := strings.TrimSpace(os.Getenv("NETVUE_CLOUDFLARE_TURN_API_TOKEN"))
	if keyID == "" && token == "" {
		return nil, nil
	}
	if !cloudflareTURNKeyID.MatchString(keyID) || token == "" || strings.ContainsAny(token, " \t\r\n") {
		return nil, errors.New("set a valid NETVUE_CLOUDFLARE_TURN_KEY_ID and NETVUE_CLOUDFLARE_TURN_API_TOKEN together")
	}
	return &cloudflareTURN{
		keyID: keyID, token: token,
		client: &http.Client{Timeout: 3 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		}},
	}, nil
}

func (turn *cloudflareTURN) credentials(ctx context.Context) ([]webrtc.ICEServer, time.Duration) {
	turn.mu.Lock()
	defer turn.mu.Unlock()
	now := time.Now()
	if !now.Before(turn.retry) && turn.expires.Sub(now) <= turnRefreshAhead && ctx.Err() == nil {
		servers, err := turn.generate(ctx)
		if err == nil {
			turn.servers, turn.expires = servers, now.Add(cloudflareTURNTTL)
		} else if ctx.Err() == nil {
			turn.retry = time.Now().Add(turnRetryDelay)
			slog.Warn("Netvue Cloudflare TURN credentials unavailable", "reason", err)
		}
	}
	remaining := time.Until(turn.expires)
	// A new session must have enough time to run even if a refresh failed.
	if remaining <= webRTCLifetime {
		return nil, 0
	}
	return turn.servers, remaining
}

func (turn *cloudflareTURN) generate(ctx context.Context) ([]webrtc.ICEServer, error) {
	url := "https://rtc.live.cloudflare.com/v1/turn/keys/" + turn.keyID + "/credentials/generate-ice-servers"
	body := fmt.Sprintf(`{"ttl":%d}`, int(cloudflareTURNTTL.Seconds()))
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, strings.NewReader(body))
	if err != nil {
		return nil, errors.New("invalid credential request")
	}
	req.Header.Set("Authorization", "Bearer "+turn.token)
	req.Header.Set("Content-Type", "application/json")
	response, err := turn.client.Do(req)
	if err != nil {
		return nil, errors.New("Cloudflare API connection failed")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		if response.StatusCode == http.StatusNotFound {
			var failure struct {
				Error string `json:"error"`
			}
			if json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&failure) == nil &&
				failure.Error == "cannot find specified key" {
				return nil, errors.New("Cloudflare API HTTP 404: TURN key not found; verify NETVUE_CLOUDFLARE_TURN_KEY_ID and the token issued for that key")
			}
		}
		return nil, fmt.Errorf("Cloudflare API HTTP %d", response.StatusCode)
	}
	var result struct {
		Servers []struct {
			URLs       []string `json:"urls"`
			Username   string   `json:"username"`
			Credential string   `json:"credential"`
		} `json:"iceServers"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 16<<10)).Decode(&result); err != nil {
		return nil, errors.New("invalid Cloudflare ICE response")
	}
	for _, server := range result.Servers {
		if server.Username == "" || server.Credential == "" {
			continue
		}
		var urls []string
		for _, supported := range []string{
			"turn:turn.cloudflare.com:3478?transport=udp",
			"turns:turn.cloudflare.com:443?transport=tcp",
		} {
			for _, candidate := range server.URLs {
				if candidate == supported {
					urls = append(urls, supported)
					break
				}
			}
		}
		if len(urls) > 0 {
			return []webrtc.ICEServer{{URLs: urls, Username: server.Username, Credential: server.Credential}}, nil
		}
	}
	return nil, errors.New("Cloudflare ICE response does not contain supported TURN servers")
}

func (manager *Manager) handleWebRTCICE(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	if manager.turn == nil {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	servers, remaining := manager.turn.credentials(r.Context())
	if len(servers) == 0 {
		writeAPIError(w, http.StatusServiceUnavailable, "Cloudflare TURN credentials unavailable")
		return
	}
	writeJSON(w, http.StatusOK, struct {
		Servers   []webrtc.ICEServer `json:"ice_servers"`
		ExpiresIn int                `json:"expires_in"`
	}{servers, int(remaining.Seconds())})
}
