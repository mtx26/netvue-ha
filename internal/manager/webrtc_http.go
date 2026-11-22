package manager

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"time"

	"github.com/pion/webrtc/v4"
)

const (
	webRTCLongPoll = 20 * time.Second
	tombstoneTTL   = 10 * time.Minute
	maxTombstones  = 1024
)

func webRTCKey(host, id string) string { return host + "/" + id }

func webRTCPath(r *http.Request) (string, string, bool) {
	host, err := normalizeCameraHost(r.PathValue("host"))
	id := r.PathValue("session_id")
	if err != nil || len(id) == 0 || len(id) > 128 {
		return "", "", false
	}
	for _, c := range id {
		if !(c >= 'a' && c <= 'z') && !(c >= 'A' && c <= 'Z') &&
			!(c >= '0' && c <= '9') && c != '_' && c != '-' {
			return "", "", false
		}
	}
	return host, id, true
}

func decodeWebRTCJSON(w http.ResponseWriter, r *http.Request, max int64, value any) bool {
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, max))
	d.DisallowUnknownFields()
	if err := d.Decode(value); err != nil {
		return false
	}
	var trailing any
	return errors.Is(d.Decode(&trailing), io.EOF)
}

func (manager *Manager) handleWebRTCOffer(w http.ResponseWriter, r *http.Request) {
	host, id, ok := webRTCPath(r)
	var request struct {
		Offer string `json:"offer"`
	}
	if !ok || !decodeWebRTCJSON(w, r, 132<<10, &request) || len(request.Offer) == 0 || len(request.Offer) > 128<<10 {
		writeAPIError(w, http.StatusBadRequest, "invalid WebRTC offer")
		return
	}
	microphone, err := offeredMicrophone(request.Offer)
	if err != nil {
		writeAPIError(w, http.StatusBadRequest, "invalid WebRTC offer")
		return
	}
	cameraAudio, err := offeredCameraAudio(request.Offer)
	if err != nil {
		writeAPIError(w, http.StatusBadRequest, "invalid WebRTC offer")
		return
	}
	var ffmpeg string
	if microphone || cameraAudio {
		ffmpeg, err = manager.webRTCFFmpeg()
		if err != nil {
			writeAPIError(w, http.StatusServiceUnavailable, "audio encoder unavailable")
			return
		}
	}
	manager.mu.Lock()
	if manager.closed || manager.ctx.Err() != nil {
		manager.mu.Unlock()
		writeAPIError(w, http.StatusServiceUnavailable, "manager unavailable")
		return
	}
	camera := manager.cameras[host]
	if camera == nil {
		manager.mu.Unlock()
		writeAPIError(w, http.StatusNotFound, "camera not found")
		return
	}
	key := webRTCKey(host, id)
	manager.purgeTombstonesLocked()
	if camera.State != stateSupervising || manager.peers[key] != nil ||
		manager.hostPeerCountLocked(host) >= webRTCMaxPeersPerHost || !manager.tombstones[key].IsZero() {
		manager.mu.Unlock()
		writeAPIError(w, http.StatusConflict, "WebRTC session unavailable")
		return
	}
	p := newWebRTCPeer(manager.ctx, host, id)
	manager.peers[key] = p
	sessionDone := camera.done
	url := camera.RTSPURL
	manager.wg.Add(1)
	manager.mu.Unlock()
	go manager.watchPeer(p, sessionDone, webRTCEstablishTimeout)
	stop := context.AfterFunc(r.Context(), p.close)
	err = p.start(manager, url, request.Offer, ffmpeg, microphone, cameraAudio)
	stop()
	if err != nil {
		p.close()
		manager.removePeer(p)
		switch {
		case errors.Is(err, errWebRTCCodecMismatch):
			slog.Warn("Netvue WebRTC negotiation failed", "reason", "codec_mismatch")
			writeAPIError(w, http.StatusBadRequest, "incompatible WebRTC codecs")
		case errors.Is(err, errWebRTCVideoEncoderMissing):
			slog.Warn("Netvue WebRTC negotiation failed", "reason", "video_encoder_unavailable")
			writeAPIError(w, http.StatusServiceUnavailable, "video encoder unavailable")
		default:
			slog.Warn("Netvue WebRTC negotiation failed", "camera", host, "error", err)
			writeAPIError(w, http.StatusServiceUnavailable, "WebRTC session unavailable")
		}
		return
	}
	close(p.prepared)
	w.WriteHeader(http.StatusCreated)
}

func (manager *Manager) watchPeer(p *webRTCPeer, sessionDone <-chan struct{}, establishTimeout time.Duration) {
	defer manager.wg.Done()
	timer := time.NewTimer(establishTimeout)
	defer timer.Stop()
	select {
	case <-sessionDone:
		p.fail("camera_stopped")
	case <-p.ctx.Done():
		p.fail("session_closed")
	case <-timer.C:
		p.fail("startup_timeout")
	case <-p.prepared:
		timer.Reset(establishTimeout)
	}
	if p.ctx.Err() == nil {
		select {
		case <-sessionDone:
			p.fail("camera_stopped")
		case <-p.ctx.Done():
			p.fail("session_closed")
		case <-timer.C:
			p.fail("connection_timeout")
		case <-p.connected:
			select {
			case <-sessionDone:
				p.fail("camera_stopped")
			case <-p.ctx.Done():
				p.fail("session_closed")
			}
		}
	}
	<-p.workersStopped
	manager.removePeer(p)
}

func (manager *Manager) removePeer(p *webRTCPeer) {
	manager.mu.Lock()
	if manager.peers[p.key()] == p {
		delete(manager.peers, p.key())
	}
	manager.mu.Unlock()
}

func (manager *Manager) hostPeerCountLocked(host string) int {
	count := 0
	for _, p := range manager.peers {
		if p.host == host {
			count++
		}
	}
	return count
}

func (manager *Manager) hostPeers(host string) []*webRTCPeer {
	manager.mu.RLock()
	defer manager.mu.RUnlock()
	var peers []*webRTCPeer
	for _, p := range manager.peers {
		if p.host == host {
			peers = append(peers, p)
		}
	}
	return peers
}

func (manager *Manager) webRTCPeer(host, id string) *webRTCPeer {
	manager.mu.RLock()
	defer manager.mu.RUnlock()
	return manager.peers[webRTCKey(host, id)]
}

func (manager *Manager) handleWebRTCCandidate(w http.ResponseWriter, r *http.Request) {
	host, id, ok := webRTCPath(r)
	var request struct {
		Candidate webrtc.ICECandidateInit `json:"candidate"`
	}
	if !ok || !decodeWebRTCJSON(w, r, 12<<10, &request) || len(request.Candidate.Candidate) > 8192 {
		writeAPIError(w, http.StatusBadRequest, "invalid WebRTC candidate")
		return
	}
	p := manager.webRTCPeer(host, id)
	if p == nil {
		writeAPIError(w, http.StatusNotFound, "WebRTC session not found")
		return
	}
	p.mu.Lock()
	pc, closed := p.pc, p.closed
	if !closed && pc != nil && p.remoteCandidates < webRTCMaxEvents {
		p.remoteCandidates++
	} else if !closed && pc != nil {
		p.mu.Unlock()
		writeAPIError(w, http.StatusTooManyRequests, "WebRTC candidate limit reached")
		return
	}
	p.mu.Unlock()
	if closed || pc == nil {
		writeAPIError(w, http.StatusConflict, "WebRTC session closed")
		return
	}
	if err := pc.AddICECandidate(request.Candidate); err != nil {
		writeAPIError(w, http.StatusBadRequest, "invalid WebRTC candidate")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (manager *Manager) handleWebRTCEvents(w http.ResponseWriter, r *http.Request) {
	host, id, ok := webRTCPath(r)
	after, err := strconv.ParseUint(r.URL.Query().Get("after"), 10, 64)
	if !ok || err != nil {
		writeAPIError(w, http.StatusBadRequest, "invalid event cursor")
		return
	}
	p := manager.webRTCPeer(host, id)
	if p == nil {
		writeAPIError(w, http.StatusNotFound, "WebRTC session not found")
		return
	}
	timer := time.NewTimer(webRTCLongPoll)
	defer timer.Stop()
	for {
		p.mu.Lock()
		if after > uint64(len(p.events)) {
			p.mu.Unlock()
			writeAPIError(w, http.StatusBadRequest, "invalid event cursor")
			return
		}
		if after < uint64(len(p.events)) {
			events := append([]webRTCEvent(nil), p.events[after:]...)
			p.mu.Unlock()
			writeJSON(w, http.StatusOK, map[string]any{"events": events})
			return
		}
		closed, notify := p.closed, p.notify
		p.mu.Unlock()
		if closed {
			writeAPIError(w, http.StatusGone, "WebRTC session closed")
			return
		}
		select {
		case <-notify:
		case <-timer.C:
			writeJSON(w, http.StatusOK, map[string]any{"events": []webRTCEvent{}})
			return
		case <-r.Context().Done():
			return
		}
	}
}

func (manager *Manager) purgeTombstonesLocked() {
	now := time.Now()
	for key, until := range manager.tombstones {
		if !now.Before(until) {
			delete(manager.tombstones, key)
		}
	}
}

func (manager *Manager) handleWebRTCClose(w http.ResponseWriter, r *http.Request) {
	host, id, ok := webRTCPath(r)
	if !ok {
		writeAPIError(w, http.StatusBadRequest, "invalid WebRTC session")
		return
	}

	manager.mu.Lock()
	manager.purgeTombstonesLocked()
	key := webRTCKey(host, id)
	p := manager.peers[key]
	active := p != nil
	if _, exists := manager.tombstones[key]; !exists && len(manager.tombstones) >= maxTombstones && !active {
		manager.mu.Unlock()
		writeAPIError(w, http.StatusServiceUnavailable, "WebRTC capacity reached")
		return
	}
	manager.tombstones[key] = time.Now().Add(tombstoneTTL)
	if !active {
		p = nil
	}
	manager.mu.Unlock()
	if p != nil {
		p.close()
		if err := waitPeerWorkers(r.Context(), p); err != nil {
			return
		}
		manager.removePeer(p)
	}
	w.WriteHeader(http.StatusNoContent)
}

func waitPeerWorkers(ctx context.Context, p *webRTCPeer) error {
	select {
	case <-p.workersStopped:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
