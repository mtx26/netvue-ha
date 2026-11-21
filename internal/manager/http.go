package manager

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net"
	"net/http"
	"os"
	"os/exec"
	"strconv"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue"
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
	mux.HandleFunc("GET /api/v1/cameras/{host}/recording", manager.handleRecordingStatus)
	mux.HandleFunc("POST /api/v1/cameras/{host}/recording/start", manager.handleRecordingStart)
	mux.HandleFunc("POST /api/v1/cameras/{host}/recording/stop", manager.handleRecordingStop)
	mux.HandleFunc("PUT /api/v1/cameras/{host}/recording/continuous", manager.handleRecordingContinuous)
	mux.HandleFunc("GET /api/v1/cameras/{host}/recordings", manager.handleRecordingsList)
	mux.HandleFunc("POST /api/v1/cameras/{host}/ai/metadata", manager.handleAIMetadataIngest)
	mux.HandleFunc("GET /api/v1/cameras/{host}/ai/metadata", manager.handleAIMetadataRange)
	mux.HandleFunc(
		"GET /api/v1/cameras/{host}/recordings/{id}/metadata",
		manager.handleRecordingMetadata,
	)
	mux.HandleFunc("GET /api/v1/cameras/{host}/recordings/{id}/download", manager.handleRecordingDownload)
	mux.HandleFunc("GET /api/v1/cameras/{host}/recordings/{id}/thumbnail", manager.handleRecordingThumbnail)
	mux.HandleFunc("DELETE /api/v1/cameras/{host}/recordings/{id}", manager.handleRecordingDelete)
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
	discovered, err := manager.discoverLAN(ctx, manager.source)
	if err != nil {
		writeManagerError(w, err)
		return
	}
	cameras, err := manager.cacheDiscoveryResults(discovered)
	if err != nil {
		writeManagerError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string][]netvue.DiscoveryResult{"cameras": cameras})
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

type continuousRecordingRequest struct {
	Enabled bool `json:"enabled"`
}

func (manager *Manager) recordingCamera(w http.ResponseWriter, r *http.Request) (*Camera, bool) {
	host, err := normalizeCameraHost(r.PathValue("host"))
	if err != nil {
		writeManagerError(w, err)
		return nil, false
	}
	camera, found := manager.Camera(host)
	if !found {
		writeAPIError(w, http.StatusNotFound, ErrCameraNotFound.Error())
		return nil, false
	}
	return &camera, true
}

func (manager *Manager) handleRecordingStatus(w http.ResponseWriter, r *http.Request) {
	camera, ok := manager.recordingCamera(w, r)
	if !ok {
		return
	}
	writeJSON(w, http.StatusOK, manager.recordings.status(camera.StreamName))
}

func (manager *Manager) handleRecordingStart(w http.ResponseWriter, r *http.Request) {
	camera, ok := manager.recordingCamera(w, r)
	if !ok {
		return
	}
	status, err := manager.recordings.setMode(camera.StreamName, "manual", true)
	if err != nil {
		writeAPIError(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, status)
}

func (manager *Manager) handleRecordingStop(w http.ResponseWriter, r *http.Request) {
	camera, ok := manager.recordingCamera(w, r)
	if !ok {
		return
	}
	status, err := manager.recordings.setMode(camera.StreamName, "manual", false)
	if err != nil {
		writeAPIError(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, status)
}

func (manager *Manager) handleRecordingContinuous(w http.ResponseWriter, r *http.Request) {
	camera, ok := manager.recordingCamera(w, r)
	if !ok {
		return
	}
	var request continuousRecordingRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&request); err != nil {
		writeAPIError(w, http.StatusBadRequest, "invalid recording request")
		return
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		writeAPIError(w, http.StatusBadRequest, "request must contain one JSON object")
		return
	}
	status, err := manager.recordings.setMode(camera.StreamName, "continuous", request.Enabled)
	if err != nil {
		writeAPIError(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, status)
}

func (manager *Manager) handleRecordingsList(w http.ResponseWriter, r *http.Request) {
	camera, ok := manager.recordingCamera(w, r)
	if !ok {
		return
	}
	var start, end time.Time
	if value := r.URL.Query().Get("start"); value != "" {
		parsed, err := time.Parse(time.RFC3339, value)
		if err != nil {
			writeAPIError(w, http.StatusBadRequest, "invalid start time")
			return
		}
		start = parsed
	}
	if value := r.URL.Query().Get("end"); value != "" {
		parsed, err := time.Parse(time.RFC3339, value)
		if err != nil {
			writeAPIError(w, http.StatusBadRequest, "invalid end time")
			return
		}
		end = parsed
	}
	if !end.IsZero() && !start.IsZero() && end.Before(start) {
		writeAPIError(w, http.StatusBadRequest, "end time precedes start time")
		return
	}
	segments, err := manager.recordings.list(camera.StreamName, start, end)
	if err != nil {
		writeAPIError(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"recordings": segments})
}

func (manager *Manager) handleAIMetadataIngest(w http.ResponseWriter, r *http.Request) {
	camera, ok := manager.recordingCamera(w, r)
	if !ok {
		return
	}
	if manager.aiMetadata == nil {
		writeAPIError(w, http.StatusServiceUnavailable, "AI metadata storage is not configured")
		return
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxAIMetadataLineBytes))
	decoder.DisallowUnknownFields()
	var event AIDetectionMetadata
	if err := decoder.Decode(&event); err != nil {
		writeAPIError(w, http.StatusBadRequest, "invalid AI metadata")
		return
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		writeAPIError(w, http.StatusBadRequest, "request must contain one JSON object")
		return
	}
	if event.StreamName != "" && event.StreamName != camera.StreamName {
		writeAPIError(w, http.StatusBadRequest, "AI metadata stream does not match camera")
		return
	}
	event.StreamName = camera.StreamName
	if err := validateAIMetadata(event); err != nil {
		writeAPIError(w, http.StatusBadRequest, err.Error())
		return
	}
	stored, err := manager.aiMetadata.add(event)
	if err != nil {
		writeAPIError(w, http.StatusInternalServerError, "unable to persist AI metadata")
		return
	}
	status := http.StatusCreated
	if !stored {
		status = http.StatusOK
	}
	writeJSON(w, status, map[string]bool{"stored": stored})
}

func (manager *Manager) handleAIMetadataRange(w http.ResponseWriter, r *http.Request) {
	camera, ok := manager.recordingCamera(w, r)
	if !ok {
		return
	}
	start, end, valid := parseMetadataRange(r)
	if !valid {
		writeAPIError(w, http.StatusBadRequest, "invalid metadata time range")
		return
	}
	manager.writeMetadataForRange(w, camera.StreamName, start, end)
}

func (manager *Manager) handleRecordingMetadata(w http.ResponseWriter, r *http.Request) {
	camera, ok := manager.recordingCamera(w, r)
	if !ok {
		return
	}
	if manager.aiMetadata == nil {
		writeAPIError(w, http.StatusServiceUnavailable, "AI metadata storage is not configured")
		return
	}
	segment, found, err := manager.recordings.findSegment(camera.StreamName, r.PathValue("id"))
	if err != nil {
		writeAPIError(w, http.StatusBadGateway, err.Error())
		return
	}
	if !found {
		writeAPIError(w, http.StatusNotFound, "recording not found")
		return
	}
	end := segment.End
	if !end.After(segment.Start) {
		end = time.Now()
	}
	events := manager.aiMetadata.between(camera.StreamName, segment.Start, end)
	writeJSON(w, http.StatusOK, map[string]any{"events": events})
}

func (manager *Manager) writeMetadataForRange(
	w http.ResponseWriter,
	streamName string,
	start, end time.Time,
) {
	if manager.aiMetadata == nil {
		writeAPIError(w, http.StatusServiceUnavailable, "AI metadata storage is not configured")
		return
	}
	events := manager.aiMetadata.between(streamName, start, end)
	segments, err := manager.recordings.list(streamName, time.Time{}, time.Time{})
	if err != nil {
		writeAPIError(w, http.StatusBadGateway, err.Error())
		return
	}
	matching := make([]AIDetectionMetadata, 0, len(events))
	for _, event := range events {
		eventTime := timestampNanos(event.Timestamp)
		for _, segment := range segments {
			segmentEnd := segment.End
			if !segmentEnd.After(segment.Start) {
				segmentEnd = time.Now()
			}
			if eventTime >= segment.Start.UnixNano() && eventTime < segmentEnd.UnixNano() {
				matching = append(matching, event)
				break
			}
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"events": matching})
}

func parseMetadataRange(r *http.Request) (time.Time, time.Time, bool) {
	var start, end time.Time
	if value := r.URL.Query().Get("start"); value != "" {
		parsed, err := time.Parse(time.RFC3339Nano, value)
		if err != nil {
			return time.Time{}, time.Time{}, false
		}
		start = parsed
	}
	if value := r.URL.Query().Get("end"); value != "" {
		parsed, err := time.Parse(time.RFC3339Nano, value)
		if err != nil {
			return time.Time{}, time.Time{}, false
		}
		end = parsed
	}
	if !end.IsZero() && !start.IsZero() && end.Before(start) {
		return time.Time{}, time.Time{}, false
	}
	return start, end, true
}

func (manager *Manager) handleRecordingDownload(w http.ResponseWriter, r *http.Request) {
	camera, ok := manager.recordingCamera(w, r)
	if !ok {
		return
	}
	segment, found, err := manager.recordings.findSegment(camera.StreamName, r.PathValue("id"))
	if err != nil {
		writeAPIError(w, http.StatusBadGateway, err.Error())
		return
	}
	if !found {
		writeAPIError(w, http.StatusNotFound, "recording not found")
		return
	}
	if raw := r.URL.Query().Get("duration"); raw != "" {
		duration, err := strconv.ParseFloat(raw, 64)
		if err != nil || math.IsNaN(duration) || math.IsInf(duration, 0) ||
			duration <= 0 || duration > segment.Duration {
			writeAPIError(w, http.StatusBadRequest, "invalid recording duration")
			return
		}
		segment.Duration = duration
	}
	select {
	case manager.recordings.playbackSlots <- struct{}{}:
		defer func() { <-manager.recordings.playbackSlots }()
	case <-r.Context().Done():
		return
	case <-manager.ctx.Done():
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Minute)
	defer cancel()
	stop := context.AfterFunc(manager.ctx, cancel)
	defer stop()
	playback, err := manager.recordings.playbackRequest(ctx, camera.StreamName, segment)
	if err != nil {
		writeAPIError(w, http.StatusBadGateway, err.Error())
		return
	}
	defer playback.Body.Close()
	if playback.StatusCode != http.StatusOK {
		writeAPIError(w, http.StatusBadGateway, fmt.Sprintf("MediaMTX playback returned HTTP %d", playback.StatusCode))
		return
	}
	file, err := os.CreateTemp(manager.recordings.dir, ".netvue-playback-*.tmp")
	if err != nil {
		slog.Error("Unable to create recording playback file", "error", err)
		writeAPIError(w, http.StatusInternalServerError, "unable to prepare recording playback")
		return
	}
	defer os.Remove(file.Name())
	defer file.Close()
	size, err := io.Copy(file, io.LimitReader(playback.Body, maxPlaybackBytes+1))
	if err != nil {
		slog.Warn("Unable to prepare recording playback", "error", err)
		writeAPIError(w, http.StatusBadGateway, "unable to prepare recording playback")
		return
	}
	if size > maxPlaybackBytes {
		writeAPIError(w, http.StatusBadGateway, "recording exceeds playback size limit")
		return
	}
	w.Header().Set("Content-Type", "video/mp4")
	w.Header().Set("Content-Disposition",
		fmt.Sprintf(`attachment; filename="netvue-%s-%s.mp4"`,
			camera.StreamName, segment.Start.UTC().Format("20060102T150405Z")))
	w.Header().Set("ETag", fmt.Sprintf(`"%s-%.9f"`, segment.ID, segment.Duration))
	http.ServeContent(w, r, "", time.Time{}, file)
}

func (manager *Manager) handleRecordingThumbnail(w http.ResponseWriter, r *http.Request) {
	camera, ok := manager.recordingCamera(w, r)
	if !ok {
		return
	}
	segment, found, err := manager.recordings.findSegment(camera.StreamName, r.PathValue("id"))
	if err != nil {
		writeAPIError(w, http.StatusBadGateway, err.Error())
		return
	}
	if !found {
		writeAPIError(w, http.StatusNotFound, "recording not found")
		return
	}
	if segment.Duration <= 0 {
		writeAPIError(w, http.StatusNotFound, "recording is not ready")
		return
	}
	var offset float64
	if raw := r.URL.Query().Get("offset"); raw != "" {
		var err error
		offset, err = strconv.ParseFloat(raw, 64)
		if err != nil || math.IsNaN(offset) || math.IsInf(offset, 0) ||
			offset < 0 || offset >= segment.Duration {
			writeAPIError(w, http.StatusBadRequest, "invalid thumbnail offset")
			return
		}
	}
	select {
	case manager.recordings.playbackSlots <- struct{}{}:
		defer func() { <-manager.recordings.playbackSlots }()
	case <-r.Context().Done():
		return
	case <-manager.ctx.Done():
		return
	}
	ffmpeg, err := manager.webRTCFFmpeg()
	if err != nil {
		writeAPIError(w, http.StatusServiceUnavailable, "thumbnail decoder unavailable")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()
	stop := context.AfterFunc(manager.ctx, cancel)
	defer stop()
	anchor := max(offset-3, 0)
	segment.Start = segment.Start.Add(time.Duration(anchor * float64(time.Second)))
	segment.Duration = min(segment.Duration-anchor, offset-anchor+2)
	cmd := exec.CommandContext(ctx, ffmpeg, "-hide_banner", "-loglevel", "error",
		"-threads", "1", "-i", manager.recordings.playbackURL(camera.StreamName, segment, "fmp4"),
		"-ss", strconv.FormatFloat(offset-anchor, 'f', 3, 64),
		"-frames:v", "1", "-vf", "scale=320:180:force_original_aspect_ratio=decrease:force_divisible_by=2",
		"-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1")
	image, err := cmd.Output()
	if err != nil {
		slog.Warn("Unable to generate recording thumbnail", "error", err)
		writeAPIError(w, http.StatusBadGateway, "unable to generate recording thumbnail")
		return
	}
	if len(image) == 0 {
		writeAPIError(w, http.StatusBadGateway, "unable to generate recording thumbnail")
		return
	}
	w.Header().Set("Content-Type", "image/jpeg")
	w.Header().Set("Cache-Control", "private, max-age=300")
	_, _ = w.Write(image)
}

func (manager *Manager) handleRecordingDelete(w http.ResponseWriter, r *http.Request) {
	camera, ok := manager.recordingCamera(w, r)
	if !ok {
		return
	}
	if err := manager.recordings.deleteSegment(camera.StreamName, r.PathValue("id")); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			writeAPIError(w, http.StatusNotFound, "recording not found")
			return
		}
		writeAPIError(w, http.StatusBadGateway, err.Error())
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
