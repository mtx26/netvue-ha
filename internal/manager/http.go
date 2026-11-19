package manager

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"time"
)

type addCameraRequest struct {
	Host       string `json:"host"`
	StreamName string `json:"stream_name"`
}

func (manager *Manager) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/v1/status", manager.handleStatus)
	mux.HandleFunc("POST /api/v1/discovery", manager.handleDiscovery)
	mux.HandleFunc("POST /api/v1/cameras", manager.handleAddCamera)
	mux.HandleFunc("GET /api/v1/cameras/{host}", manager.handleGetCamera)
	mux.HandleFunc("POST /api/v1/cameras/{host}/start", manager.handleStartCamera)
	mux.HandleFunc("POST /api/v1/cameras/{host}/stop", manager.handleStopCamera)
	mux.HandleFunc("DELETE /api/v1/cameras/{host}", manager.handleDeleteCamera)
	mux.HandleFunc("GET /api/v1/webrtc/ice", manager.handleWebRTCICE)
	mux.HandleFunc("PUT /api/v1/cameras/{host}/webrtc/{session_id}", manager.handleWebRTCOffer)
	mux.HandleFunc("POST /api/v1/cameras/{host}/webrtc/{session_id}/candidates", manager.handleWebRTCCandidate)
	mux.HandleFunc("GET /api/v1/cameras/{host}/webrtc/{session_id}/events", manager.handleWebRTCEvents)
	mux.HandleFunc("DELETE /api/v1/cameras/{host}/webrtc/{session_id}", manager.handleWebRTCClose)
	return mux
}

func (manager *Manager) handleDiscovery(w http.ResponseWriter, r *http.Request) {
	if !manager.discoveryMu.TryLock() {
		writeAPIError(w, http.StatusConflict, "discovery already in progress")
		return
	}
	defer manager.discoveryMu.Unlock()
	manager.mu.RLock()
	closed := manager.closed || manager.ctx.Err() != nil
	manager.mu.RUnlock()
	if closed {
		writeManagerError(w, ErrManagerClosed)
		return
	}
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	stop := context.AfterFunc(manager.ctx, cancel)
	defer stop()
	hosts, err := manager.discoverLAN(ctx, manager.source)
	if err != nil {
		writeManagerError(w, err)
		return
	}
	type discoveredCamera struct {
		Host string `json:"host"`
	}
	cameras := make([]discoveredCamera, 0, len(hosts))
	for _, host := range hosts {
		cameras = append(cameras, discoveredCamera{Host: host})
	}
	writeJSON(w, http.StatusOK, map[string][]discoveredCamera{"cameras": cameras})
}

func (manager *Manager) handleStatus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string][]Camera{"cameras": manager.Cameras()})
}

func (manager *Manager) handleAddCamera(w http.ResponseWriter, r *http.Request) {
	var request addCameraRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeAPIError(w, http.StatusBadRequest, "invalid camera request")
		return
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		writeAPIError(w, http.StatusBadRequest, "request must contain one JSON object")
		return
	}
	if r.Context().Err() != nil {
		return
	}
	camera, added, err := manager.AddCamera(request.Host, request.StreamName)
	if err != nil {
		writeManagerError(w, err)
		return
	}
	status := http.StatusOK
	if added {
		status = http.StatusAccepted
	}
	writeJSON(w, status, camera)
}

func (manager *Manager) handleGetCamera(w http.ResponseWriter, r *http.Request) {
	host := r.PathValue("host")
	if _, err := normalizeCameraHost(host); err != nil {
		writeManagerError(w, err)
		return
	}
	camera, found := manager.Camera(host)
	if !found {
		writeAPIError(w, http.StatusNotFound, ErrCameraNotFound.Error())
		return
	}
	writeJSON(w, http.StatusOK, camera)
}

func (manager *Manager) handleStartCamera(w http.ResponseWriter, r *http.Request) {
	if r.Context().Err() != nil {
		return
	}
	camera, started, err := manager.StartCamera(r.PathValue("host"))
	if err != nil {
		writeManagerError(w, err)
		return
	}
	status := http.StatusOK
	if started {
		status = http.StatusAccepted
	}
	writeJSON(w, status, camera)
}

func (manager *Manager) handleStopCamera(w http.ResponseWriter, r *http.Request) {
	camera, err := manager.StopCamera(r.Context(), r.PathValue("host"))
	if err != nil {
		writeManagerError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, camera)
}

func (manager *Manager) handleDeleteCamera(w http.ResponseWriter, r *http.Request) {
	deleted, err := manager.DeleteCamera(r.Context(), r.PathValue("host"))
	if err != nil {
		writeManagerError(w, err)
		return
	}
	if !deleted {
		writeAPIError(w, http.StatusNotFound, ErrCameraNotFound.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "deleted"})
}

func writeManagerError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, ErrInvalidCamera):
		writeAPIError(w, http.StatusBadRequest, err.Error())
	case errors.Is(err, ErrCameraNotFound):
		writeAPIError(w, http.StatusNotFound, err.Error())
	case errors.Is(err, ErrCameraConflict):
		writeAPIError(w, http.StatusConflict, err.Error())
	case errors.Is(err, ErrManagerClosed),
		errors.Is(err, context.Canceled),
		errors.Is(err, context.DeadlineExceeded):
		writeAPIError(w, http.StatusServiceUnavailable, err.Error())
	default:
		writeAPIError(w, http.StatusInternalServerError, err.Error())
	}
}

func writeAPIError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(body); err != nil {
		return
	}
}

func Run(ctx context.Context, source, listenAddress string) error {
	if ctx == nil {
		return errors.New("service context is required")
	}
	if err := validateLoopbackAddress(listenAddress); err != nil {
		return err
	}
	turn, err := cloudflareTURNFromEnv()
	if err != nil {
		return err
	}
	manager, err := New(ctx, source)
	if err != nil {
		return err
	}
	manager.turn = turn
	listener, err := net.Listen("tcp", listenAddress)
	if err != nil {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		if closeErr := manager.Close(shutdownCtx); closeErr != nil {
			return errors.Join(fmt.Errorf("listen for control API: %w", err), closeErr)
		}
		return fmt.Errorf("listen for control API: %w", err)
	}
	server := &http.Server{
		Handler:           manager.Handler(),
		ReadHeaderTimeout: 3 * time.Second,
		ReadTimeout:       5 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    16 << 10,
	}
	serveResult := make(chan error, 1)
	go func() {
		serveResult <- server.Serve(listener)
	}()

	var serveErr error
	select {
	case err := <-serveResult:
		if !errors.Is(err, http.ErrServerClosed) {
			serveErr = err
		}
	case <-ctx.Done():
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	managerErr := manager.Close(shutdownCtx)
	serverErr := server.Shutdown(shutdownCtx)
	if serverErr != nil {
		_ = server.Close()
	}
	if serveErr != nil {
		serveErr = fmt.Errorf("serve control API: %w", serveErr)
	}
	if managerErr != nil {
		managerErr = fmt.Errorf("stop camera sessions: %w", managerErr)
	}
	if serverErr != nil {
		serverErr = fmt.Errorf("stop control API: %w", serverErr)
	}
	return errors.Join(serveErr, managerErr, serverErr)
}

func validateLoopbackAddress(address string) error {
	host, portText, err := net.SplitHostPort(address)
	if err != nil {
		return fmt.Errorf("invalid control API address: %w", err)
	}
	ip := net.ParseIP(host)
	port, portErr := strconv.Atoi(portText)
	if ip == nil || !ip.IsLoopback() || portErr != nil || port < 1 || port > 65535 {
		return errors.New("control API must listen on a loopback IP and valid port")
	}
	return nil
}
