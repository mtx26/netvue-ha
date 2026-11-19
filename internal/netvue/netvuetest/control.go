package netvuetest

import (
	"encoding/json"
	"net/http"
	"strings"
)

// ControlHandler exposes the camera to an out-of-process test driver with the
// historical synthetic-camera API: GET /status, POST /fail-next and
// POST /fault-next/{login|live|media}/{fault}. A scheduled fault is applied to
// the next connection, and the current connection is dropped to force it.
func (c *Camera) ControlHandler() http.Handler {
	mux := http.NewServeMux()
	reply := func(w http.ResponseWriter, status int, body any) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(body)
	}
	mux.HandleFunc("GET /status", func(w http.ResponseWriter, _ *http.Request) {
		s := c.State()
		reply(w, http.StatusOK, map[string]any{
			"login_count":      s.Logins,
			"live_requests":    s.LiveRequests,
			"streaming":        s.Streaming,
			"video_frames":     s.VideoFrames,
			"talk_frames":      s.TalkFrames,
			"protocol_errors":  s.ProtocolError,
			"last_login_fault": nullable(s.LastFault["login"]),
			"last_live_fault":  nullable(s.LastFault["live"]),
			"last_media_fault": nullable(s.LastFault["media"]),
		})
	})
	mux.HandleFunc("POST /fail-next", func(w http.ResponseWriter, _ *http.Request) {
		c.Drop()
		reply(w, http.StatusAccepted, map[string]any{"dropped": true})
	})
	mux.HandleFunc("POST /fault-next/{stage}/{fault}", func(w http.ResponseWriter, r *http.Request) {
		stage, fault := r.PathValue("stage"), r.PathValue("fault")
		if err := c.FailNext(stage, fault); err != nil {
			status := http.StatusBadRequest
			if strings.Contains(err.Error(), "pending") {
				status = http.StatusConflict
			}
			reply(w, status, map[string]string{"error": err.Error()})
			return
		}
		c.Drop()
		reply(w, http.StatusAccepted, map[string]string{"scheduled_fault": fault, "stage": stage})
	})
	return mux
}

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}
