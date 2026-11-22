package manager

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	defaultRecordingControlURL  = "http://127.0.0.1:9997"
	defaultRecordingPlaybackURL = "http://127.0.0.1:9996"
	maxPlaybackBytes            = 1 << 30
)

type RecordingStatus struct {
	Manual     bool   `json:"manual"`
	Continuous bool   `json:"continuous"`
	Active     bool   `json:"active"`
	Error      string `json:"error,omitempty"`
}

type RecordingSegment struct {
	ID       string    `json:"id"`
	Start    time.Time `json:"start"`
	End      time.Time `json:"end"`
	Duration float64   `json:"duration"`
	Format   string    `json:"format"`
}

type ProtectedRecording struct {
	ID             string           `json:"id"`
	StreamName     string           `json:"stream_name"`
	CameraHost     string           `json:"camera_host,omitempty"`
	Title          string           `json:"title,omitempty"`
	Category       string           `json:"category,omitempty"`
	Protection     string           `json:"protection"`
	RequestedStart time.Time        `json:"requested_start"`
	RequestedEnd   time.Time        `json:"requested_end"`
	CoveredStart   time.Time        `json:"covered_start"`
	CoveredEnd     time.Time        `json:"covered_end"`
	CreatedAt      time.Time        `json:"created_at"`
	Size           int64            `json:"size"`
	Filename       string           `json:"filename"`
	Status         string           `json:"status"`
	Error          string           `json:"error,omitempty"`
	Gaps           []RecordingRange `json:"gaps"`
}

type RecordingRange struct {
	Start time.Time `json:"start"`
	End   time.Time `json:"end"`
}

type protectedRecordingIndex struct {
	Recordings []ProtectedRecording `json:"recordings"`
}

type recordingState struct {
	Manual     bool `json:"manual"`
	Continuous bool `json:"continuous"`
}

type recordingConfig struct {
	Cameras map[string]recordingState `json:"cameras"`
}

type recordingSettings struct {
	Retention  string `json:"recording_retention"`
	LimitBytes int64  `json:"recording_limit_bytes"`
}

type turnSettings struct {
	Enabled bool   `json:"enabled"`
	KeyID   string `json:"key_id"`
	Token   string `json:"api_token"`
}

type runtimeSettings struct {
	Recording *recordingSettings `json:"recording,omitempty"`
	TURN      *turnSettings      `json:"turn,omitempty"`
}

type mediaMTXRecording struct {
	Name     string `json:"name"`
	Segments []struct {
		Start    string   `json:"start"`
		Duration *float64 `json:"duration"`
	} `json:"segments"`
}

type recordingManager struct {
	ctx             context.Context
	dir             string
	protectedDir    string
	protectedIndex  string
	controlURL      string
	playbackAddress string
	client          *http.Client
	playbackSlots   chan struct{}
	overlaySlot     chan struct{}
	protectedSlot   chan struct{}
	protectedMu     sync.Mutex
	retentionMu     sync.RWMutex
	settingsMu      sync.Mutex
	settings        runtimeSettings
	settingsErr     error

	mu     sync.RWMutex
	states map[string]recordingState
}

func newRecordingManager(ctx context.Context) *recordingManager {
	dir := strings.TrimSpace(os.Getenv("NETVUE_RECORDINGS_DIR"))
	manager := &recordingManager{
		ctx:             ctx,
		dir:             dir,
		controlURL:      defaultRecordingControlURL,
		playbackAddress: defaultRecordingPlaybackURL,
		client:          &http.Client{Timeout: 5 * time.Second},
		playbackSlots:   make(chan struct{}, 2),
		overlaySlot:     make(chan struct{}, 1),
		protectedSlot:   make(chan struct{}, 1),
		states:          make(map[string]recordingState),
	}
	if dir == "" {
		return manager
	}
	if err := os.MkdirAll(dir, 0o750); err == nil {
		manager.load()
		manager.initProtectedStorage()
		manager.loadRuntimeSettings()
		if manager.settingsErr == nil {
			go manager.cleanupLoop()
		}
	} else {
		manager.settingsErr = err
	}
	return manager
}

func (manager *recordingManager) loadRuntimeSettings() {
	data, err := os.ReadFile(filepath.Join(manager.dir, ".netvue-settings.json"))
	if errors.Is(err, os.ErrNotExist) {
		return
	}
	if err != nil {
		manager.settingsErr = err
		return
	}
	if err := json.Unmarshal(data, &manager.settings); err != nil {
		manager.settingsErr = errors.New("invalid runtime settings")
		return
	}
	if manager.settings.Recording != nil {
		if _, err := validateRecordingSettings(*manager.settings.Recording); err != nil {
			manager.settingsErr = err
		}
	}
	if manager.settings.TURN != nil && !validTURNSettings(*manager.settings.TURN) {
		manager.settingsErr = errors.New("invalid saved TURN settings")
	}
}

func (manager *recordingManager) saveRuntimeSettings(settings runtimeSettings) error {
	if !manager.enabled() {
		return errors.New("local recording storage is not configured")
	}
	data, err := json.Marshal(settings)
	if err != nil {
		return err
	}
	return writeRecordingJSON(filepath.Join(manager.dir, ".netvue-settings.json"), data)
}

func validateRecordingSettings(settings recordingSettings) (time.Duration, error) {
	retention, err := time.ParseDuration(settings.Retention)
	if err != nil || retention <= 0 || settings.LimitBytes < 0 {
		return 0, errors.New("invalid recording settings")
	}
	return retention, nil
}

func (manager *recordingManager) recordingSettings() recordingSettings {
	manager.settingsMu.Lock()
	defer manager.settingsMu.Unlock()
	if manager.settings.Recording != nil {
		return *manager.settings.Recording
	}
	return recordingSettingsFromEnv()
}

func recordingSettingsFromEnv() recordingSettings {
	retention := strings.TrimSpace(os.Getenv("NETVUE_RECORDING_RETENTION"))
	duration, err := time.ParseDuration(retention)
	if err != nil || duration <= 0 {
		retention = "168h"
	}
	limit, err := strconv.ParseInt(strings.TrimSpace(os.Getenv("NETVUE_RECORDING_LIMIT_BYTES")), 10, 64)
	if err != nil || limit < 0 {
		limit = 0
	}
	return recordingSettings{Retention: retention, LimitBytes: limit}
}

func (manager *recordingManager) enabled() bool {
	return manager != nil && manager.dir != ""
}

func (manager *recordingManager) configPath() string {
	return filepath.Join(manager.dir, ".netvue-recordings.json")
}

func (manager *recordingManager) load() {
	manager.mu.Lock()
	defer manager.mu.Unlock()
	data, err := os.ReadFile(manager.configPath())
	if err != nil {
		return
	}
	var config recordingConfig
	if json.Unmarshal(data, &config) == nil && config.Cameras != nil {
		manager.states = config.Cameras
	}
}

func (manager *recordingManager) saveLocked() error {
	config := recordingConfig{Cameras: manager.states}
	data, err := json.MarshalIndent(config, "", "  ")
	if err != nil {
		return err
	}
	return writeRecordingJSON(manager.configPath(), data)
}

func writeRecordingJSON(path string, data []byte) error {
	pattern := strings.TrimSuffix(filepath.Base(path), ".json") + "-*.json"
	temp, err := os.CreateTemp(filepath.Dir(path), pattern)
	if err != nil {
		return err
	}
	tempName := temp.Name()
	defer os.Remove(tempName)
	if _, err := temp.Write(data); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return os.Rename(tempName, path)
}

func (manager *recordingManager) apply(streamName string) error {
	if !manager.enabled() {
		return nil
	}
	manager.mu.RLock()
	state := manager.states[streamName]
	manager.mu.RUnlock()
	return manager.setMediaMTXRecording(streamName, state.Manual || state.Continuous)
}

func (manager *recordingManager) setMode(streamName string, mode string, enabled bool) (RecordingStatus, error) {
	if !manager.enabled() {
		return RecordingStatus{}, errors.New("local recording storage is not configured")
	}
	manager.mu.Lock()
	state := manager.states[streamName]
	if mode == "continuous" {
		state.Continuous = enabled
	} else {
		state.Manual = enabled
	}
	if err := manager.setMediaMTXRecording(streamName, state.Manual || state.Continuous); err != nil {
		manager.mu.Unlock()
		return RecordingStatus{}, err
	}
	manager.states[streamName] = state
	err := manager.saveLocked()
	manager.mu.Unlock()
	if err != nil {
		return RecordingStatus{}, fmt.Errorf("save recording settings: %w", err)
	}
	return manager.statusLocked(streamName, state), nil
}

func (manager *recordingManager) status(streamName string) RecordingStatus {
	if !manager.enabled() {
		return RecordingStatus{}
	}
	manager.mu.RLock()
	state := manager.states[streamName]
	manager.mu.RUnlock()
	return manager.statusLocked(streamName, state)
}

func (manager *recordingManager) statusLocked(streamName string, state recordingState) RecordingStatus {
	status := RecordingStatus{Manual: state.Manual, Continuous: state.Continuous}
	requested := state.Manual || state.Continuous
	if !requested {
		return status
	}
	var path struct {
		Available bool `json:"available"`
	}
	res, err := manager.controlRequest(http.MethodGet, "/v3/paths/get/"+url.PathEscape(streamName), nil)
	if err == nil {
		defer res.Body.Close()
		if res.StatusCode == http.StatusOK {
			if json.NewDecoder(res.Body).Decode(&path) == nil {
				status.Active = path.Available
			}
		}
	}
	return status
}

func (manager *recordingManager) setMediaMTXRecording(streamName string, enabled bool) error {
	if !manager.enabled() {
		return nil
	}
	getURL := "/v3/config/paths/get/" + url.PathEscape(streamName)
	res, err := manager.controlRequest(http.MethodGet, getURL, nil)
	if err != nil {
		return err
	}
	defer res.Body.Close()

	payload := map[string]any{
		"record":                enabled,
		"recordFormat":          "fmp4",
		"recordPath":            "/recordings/%path/%Y-%m-%d_%H-%M-%S-%f",
		"recordPartDuration":    "1s",
		"recordSegmentDuration": "10m",
		"recordDeleteAfter":     "0s",
		"recordMaxPartSize":     "50M",
	}
	switch res.StatusCode {
	case http.StatusOK:
		err = manager.controlJSON(http.MethodPatch,
			"/v3/config/paths/patch/"+url.PathEscape(streamName), payload)
	case http.StatusNotFound:
		err = manager.controlJSON(http.MethodPost,
			"/v3/config/paths/add/"+url.PathEscape(streamName), payload)
	default:
		err = fmt.Errorf("MediaMTX control API returned HTTP %d", res.StatusCode)
	}
	return err
}

func (manager *recordingManager) controlRequest(method, path string, body any) (*http.Response, error) {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = bytes.NewReader(data)
	}
	request, err := http.NewRequestWithContext(manager.ctx, method, manager.controlURL+path, reader)
	if err != nil {
		return nil, err
	}
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	return manager.client.Do(request)
}

func (manager *recordingManager) controlJSON(method, path string, body any) error {
	res, err := manager.controlRequest(method, path, body)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		data, _ := io.ReadAll(io.LimitReader(res.Body, 4096))
		return fmt.Errorf("MediaMTX control API returned HTTP %d: %s", res.StatusCode, strings.TrimSpace(string(data)))
	}
	return nil
}

func recordingID(streamName string, start time.Time) string {
	value := fmt.Sprintf("%s|%s", streamName, start.UTC().Format(time.RFC3339Nano))
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:16])
}

func (manager *recordingManager) list(streamName string, start, end time.Time) ([]RecordingSegment, error) {
	if !manager.enabled() {
		return nil, errors.New("local recording storage is not configured")
	}
	path := "/v3/recordings/get/" + url.PathEscape(streamName)
	res, err := manager.controlRequest(http.MethodGet, path, nil)
	if err != nil {
		return nil, err
	}
	defer res.Body.Close()
	if res.StatusCode == http.StatusNotFound {
		return []RecordingSegment{}, nil
	}
	if res.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("MediaMTX control API returned HTTP %d", res.StatusCode)
	}
	var recording mediaMTXRecording
	if err := json.NewDecoder(res.Body).Decode(&recording); err != nil {
		return nil, errors.New("invalid MediaMTX recording response")
	}
	segments := make([]RecordingSegment, 0, len(recording.Segments))
	for _, item := range recording.Segments {
		startTime, err := time.Parse(time.RFC3339Nano, item.Start)
		if err != nil {
			continue
		}
		duration := 0.0
		if item.Duration != nil {
			duration = *item.Duration
		}
		if !start.IsZero() && startTime.Before(start) {
			continue
		}
		if !end.IsZero() && startTime.After(end) {
			continue
		}
		endTime := startTime.Add(time.Duration(duration * float64(time.Second)))
		segments = append(segments, RecordingSegment{
			ID:    recordingID(streamName, startTime),
			Start: startTime, End: endTime, Duration: duration, Format: "fmp4",
		})
	}
	sort.Slice(segments, func(i, j int) bool {
		return segments[i].Start.Before(segments[j].Start)
	})
	return segments, nil
}

func (manager *recordingManager) findSegment(streamName, id string) (RecordingSegment, bool, error) {
	segments, err := manager.list(streamName, time.Time{}, time.Time{})
	if err != nil {
		return RecordingSegment{}, false, err
	}
	for _, segment := range segments {
		if segment.ID == id {
			return segment, true, nil
		}
	}
	return RecordingSegment{}, false, nil
}

func (manager *recordingManager) deleteSegment(streamName, id string) error {
	manager.retentionMu.Lock()
	defer manager.retentionMu.Unlock()
	segment, found, err := manager.findSegment(streamName, id)
	if err != nil {
		return err
	}
	if !found {
		return os.ErrNotExist
	}
	query := url.Values{}
	query.Set("path", streamName)
	query.Set("start", segment.Start.UTC().Format(time.RFC3339Nano))
	res, err := manager.controlRequest(http.MethodDelete, "/v3/recordings/segments/delete?"+query.Encode(), nil)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		return fmt.Errorf("MediaMTX control API returned HTTP %d", res.StatusCode)
	}
	return nil
}

func (manager *recordingManager) playbackURL(streamName string, segment RecordingSegment, format string) string {
	query := url.Values{}
	query.Set("path", streamName)
	query.Set("start", segment.Start.UTC().Format(time.RFC3339Nano))
	query.Set("duration", fmt.Sprintf("%.9f", segment.Duration))
	query.Set("format", format)
	return manager.playbackAddress + "/get?" + query.Encode()
}

func (manager *recordingManager) playbackRequest(
	ctx context.Context, streamName string, segment RecordingSegment,
) (*http.Response, error) {
	request, err := http.NewRequestWithContext(
		ctx, http.MethodGet, manager.playbackURL(streamName, segment, "mp4"), nil,
	)
	if err != nil {
		return nil, err
	}
	client := *manager.client
	client.Timeout = 0
	return client.Do(request)
}

func (manager *recordingManager) initProtectedStorage() {
	protectedDir := strings.TrimSpace(os.Getenv("NETVUE_PROTECTED_RECORDINGS_DIR"))
	if protectedDir == "" {
		protectedDir = filepath.Join(manager.dir, "protected")
	}
	if err := os.MkdirAll(protectedDir, 0o750); err != nil {
		return
	}
	manager.protectedDir = protectedDir
	manager.protectedIndex = filepath.Join(protectedDir, ".netvue-protected.json")
	_ = manager.recoverProtectedRecordings()
}

func (manager *recordingManager) protectedEnabled() bool {
	return manager != nil && manager.protectedDir != "" && manager.protectedIndex != ""
}

func (manager *recordingManager) protectedRecordings() ([]ProtectedRecording, error) {
	if !manager.protectedEnabled() {
		return nil, errors.New("protected recording storage is not configured")
	}
	data, err := os.ReadFile(manager.protectedIndex)
	if errors.Is(err, os.ErrNotExist) {
		return []ProtectedRecording{}, nil
	}
	if err != nil {
		return nil, err
	}
	var index protectedRecordingIndex
	if err := json.Unmarshal(data, &index); err != nil {
		return nil, errors.New("protected recording index is invalid")
	}
	sort.Slice(index.Recordings, func(i, j int) bool {
		return index.Recordings[i].CreatedAt.After(index.Recordings[j].CreatedAt)
	})
	return index.Recordings, nil
}

func (manager *recordingManager) saveProtectedIndex(recordings []ProtectedRecording) error {
	data, err := json.MarshalIndent(protectedRecordingIndex{Recordings: recordings}, "", "  ")
	if err != nil {
		return err
	}
	if err := writeRecordingJSON(manager.protectedIndex, data); err != nil {
		return err
	}
	return syncProtectedDirectory(manager.protectedDir)
}

func (manager *recordingManager) recoverProtectedRecordings() error {
	recordings, err := manager.protectedRecordings()
	if err != nil {
		return err
	}
	changed := false
	for i := range recordings {
		recording := &recordings[i]
		if recording.Status != "saving" {
			continue
		}
		changed = true
		if recording.Filename == recording.ID+".mp4" && filepath.Base(recording.Filename) == recording.Filename {
			info, err := os.Lstat(filepath.Join(manager.protectedDir, recording.Filename))
			if err == nil && info.Mode().IsRegular() && info.Size() == recording.Size && info.Size() > 0 {
				recording.Status = "saved"
				continue
			}
		}
		recording.Status = "failed"
		recording.Error = "protected recording export was interrupted"
	}
	if changed {
		return manager.saveProtectedIndex(recordings)
	}
	return nil
}

func (manager *recordingManager) findProtectedRecording(id string) (ProtectedRecording, string, bool, error) {
	recordings, err := manager.protectedRecordings()
	if err != nil {
		return ProtectedRecording{}, "", false, err
	}
	for _, recording := range recordings {
		if recording.ID == id {
			if recording.Filename == "" && recording.Status == "failed" {
				return recording, "", true, nil
			}
			if recording.Filename != id+".mp4" || filepath.Base(recording.Filename) != recording.Filename {
				return ProtectedRecording{}, "", false, errors.New("invalid protected recording filename")
			}
			path := filepath.Join(manager.protectedDir, recording.Filename)
			return recording, path, true, nil
		}
	}
	return ProtectedRecording{}, "", false, nil
}

func (manager *recordingManager) deleteProtectedRecording(id string) error {
	manager.protectedMu.Lock()
	defer manager.protectedMu.Unlock()
	_, path, found, err := manager.findProtectedRecording(id)
	if err != nil {
		return err
	}
	if !found {
		return os.ErrNotExist
	}
	recordings, err := manager.protectedRecordings()
	if err != nil {
		return err
	}
	kept := make([]ProtectedRecording, 0, len(recordings))
	for _, candidate := range recordings {
		if candidate.ID != id {
			kept = append(kept, candidate)
		}
	}
	if path != "" {
		if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	return manager.saveProtectedIndex(kept)
}

func (manager *recordingManager) protectionCoverage(
	streamName string, start, end time.Time,
) ([]RecordingSegment, []RecordingRange, error) {
	segments, err := manager.list(streamName, time.Time{}, time.Time{})
	if err != nil {
		return nil, nil, err
	}
	matching := make([]RecordingSegment, 0)
	gaps := make([]RecordingRange, 0)
	cursor := start
	for _, segment := range segments {
		if !segment.End.After(segment.Start) || !segment.End.After(cursor) || !segment.Start.Before(end) {
			continue
		}
		if segment.Start.After(cursor) {
			gapEnd := segment.Start
			if gapEnd.After(end) {
				gapEnd = end
			}
			gaps = append(gaps, RecordingRange{Start: cursor, End: gapEnd})
		}
		matching = append(matching, segment)
		cursor = segment.End
		if !cursor.Before(end) {
			cursor = end
			break
		}
	}
	if cursor.Before(end) {
		gaps = append(gaps, RecordingRange{Start: cursor, End: end})
	}
	return matching, gaps, nil
}

func (manager *recordingManager) protectRange(
	ctx context.Context,
	streamName, cameraHost, title, category, protection string,
	start, end time.Time,
) (ProtectedRecording, error) {
	if !manager.protectedEnabled() {
		return ProtectedRecording{}, errors.New("protected recording storage is not configured")
	}
	if start.IsZero() || end.IsZero() || !end.After(start) {
		return ProtectedRecording{}, errors.New("invalid protected recording range")
	}
	maxDuration, err := protectedMaxDuration()
	if err != nil {
		return ProtectedRecording{}, err
	}
	if end.Sub(start) > maxDuration {
		return ProtectedRecording{}, errors.New("protected recording exceeds maximum duration")
	}
	select {
	case manager.protectedSlot <- struct{}{}:
		defer func() { <-manager.protectedSlot }()
	default:
		return ProtectedRecording{}, errors.New("protected recording export is busy")
	}
	var idBytes [16]byte
	if _, err := rand.Read(idBytes[:]); err != nil {
		return ProtectedRecording{}, err
	}
	recording := ProtectedRecording{
		ID: hex.EncodeToString(idBytes[:]), StreamName: streamName, CameraHost: cameraHost,
		Title: strings.TrimSpace(title), Category: strings.TrimSpace(category), Protection: protection,
		RequestedStart: start, RequestedEnd: end, CreatedAt: time.Now().UTC(),
	}
	result, err := manager.exportProtectedRange(ctx, recording)
	if err == nil {
		return result, nil
	}
	recording.Status = "failed"
	recording.Error = err.Error()
	recording.Gaps = result.Gaps
	manager.protectedMu.Lock()
	defer manager.protectedMu.Unlock()
	recordings, indexErr := manager.protectedRecordings()
	if indexErr == nil {
		for i, existing := range recordings {
			if existing.ID == recording.ID {
				if existing.Status == "saving" {
					existing.Status, existing.Error = "failed", err.Error()
					recordings[i] = existing
					if indexErr := manager.saveProtectedIndex(recordings); indexErr != nil {
						return existing, errors.Join(err, indexErr)
					}
				}
				return existing, err
			}
		}
		indexErr = manager.saveProtectedIndex(append(recordings, recording))
	}
	if indexErr != nil {
		return recording, errors.Join(err, fmt.Errorf("save failed recording status: %w", indexErr))
	}
	return recording, err
}

func (manager *recordingManager) exportProtectedRange(
	ctx context.Context, recording ProtectedRecording,
) (ProtectedRecording, error) {
	streamName, start, end := recording.StreamName, recording.RequestedStart, recording.RequestedEnd
	manager.retentionMu.RLock()
	defer manager.retentionMu.RUnlock()
	ctx, cancel := context.WithCancel(ctx)
	stop := context.AfterFunc(manager.ctx, cancel)
	defer stop()
	defer cancel()
	segments, gaps, err := manager.protectionCoverage(streamName, start, end)
	if err != nil {
		return ProtectedRecording{}, err
	}
	if len(gaps) != 0 {
		return ProtectedRecording{RequestedStart: start, RequestedEnd: end, Gaps: gaps, Status: "failed"},
			errors.New("recording footage has gaps in the requested range")
	}
	if _, err := manager.protectedHealth(); err != nil {
		return ProtectedRecording{}, err
	}
	ffmpeg, err := exec.LookPath("ffmpeg")
	if err != nil {
		return ProtectedRecording{}, errors.New("protected recording encoder is unavailable")
	}
	ffprobe, err := exec.LookPath("ffprobe")
	if err != nil {
		return ProtectedRecording{}, errors.New("protected recording verifier is unavailable")
	}
	output, err := os.CreateTemp(manager.protectedDir, ".protected-*.mp4")
	if err != nil {
		return ProtectedRecording{}, err
	}
	outputName := output.Name()
	output.Close()
	defer os.Remove(outputName)

	inputParts := make([]string, 0, len(segments))
	playlist, err := os.CreateTemp(manager.protectedDir, ".protected-concat-*.txt")
	if err != nil {
		return ProtectedRecording{}, err
	}
	playlistName := playlist.Name()
	defer os.Remove(playlistName)
	defer playlist.Close()

	cursor := start
	var layout []protectedStream
	for index, segment := range segments {
		overlapStart := cursor
		if segment.Start.After(overlapStart) {
			overlapStart = segment.Start
		}
		overlapEnd := end
		if segment.End.Before(overlapEnd) {
			overlapEnd = segment.End
		}
		if !overlapEnd.After(overlapStart) {
			continue
		}
		cursor = overlapEnd
		part, err := os.CreateTemp(manager.protectedDir, ".protected-part-*.mp4")
		if err != nil {
			return ProtectedRecording{}, err
		}
		partName := part.Name()
		part.Close()
		defer os.Remove(partName)
		inputParts = append(inputParts, partName)

		offset := overlapStart.Sub(segment.Start).Seconds()
		duration := overlapEnd.Sub(overlapStart).Seconds()
		source := manager.playbackURL(streamName, segment, "fmp4")
		if err := checkProtectedTimestamps(ctx, ffprobe, source); err != nil {
			return ProtectedRecording{}, fmt.Errorf("verify recording segment %d timestamps: %w", index+1, err)
		}
		if _, err := manager.protectedHealth(); err != nil {
			return ProtectedRecording{}, err
		}
		err = manager.runProtectedFFmpeg(ctx, ffmpeg,
			"-hide_banner", "-loglevel", "error", "-y",
			"-i", source,
			"-ss", fmt.Sprintf("%.9f", max(offset, 0)),
			"-t", fmt.Sprintf("%.9f", duration),
			"-map", "0:v:0", "-map", "0:a:0?",
			"-c:v", "libx264", "-threads", "1", "-preset", "veryfast", "-pix_fmt", "yuv420p",
			"-c:a", "aac", partName)
		if err != nil {
			return ProtectedRecording{}, fmt.Errorf("protect recording segment %d: %w", index+1, err)
		}
		streams, err := probeProtectedStreams(ctx, ffprobe, partName)
		if err != nil {
			return ProtectedRecording{}, fmt.Errorf("verify recording segment %d: %w", index+1, err)
		}
		if layout == nil {
			layout = streams
		} else if !reflect.DeepEqual(layout, streams) {
			return ProtectedRecording{}, errors.New("recording segment formats differ")
		}
	}
	if len(inputParts) == 0 {
		return ProtectedRecording{}, errors.New("no overlapping recording footage is available")
	}
	for _, partName := range inputParts {
		if _, err := fmt.Fprintf(playlist, "file '%s'\n", strings.ReplaceAll(filepath.ToSlash(partName), "'", "'\\''")); err != nil {
			return ProtectedRecording{}, err
		}
	}
	if err := playlist.Sync(); err != nil {
		return ProtectedRecording{}, err
	}
	playlist.Close()

	if _, err := manager.protectedHealth(); err != nil {
		return ProtectedRecording{}, err
	}
	err = manager.runProtectedFFmpeg(ctx, ffmpeg,
		"-hide_banner", "-loglevel", "error", "-y",
		"-f", "concat", "-safe", "0", "-i", playlistName,
		"-map", "0:v?", "-map", "0:a?", "-c", "copy", outputName)
	if err != nil {
		return ProtectedRecording{}, fmt.Errorf("create protected recording: %w", err)
	}
	if err := manager.runProtectedFFmpeg(ctx, ffmpeg, "-hide_banner", "-loglevel", "error", "-xerror",
		"-i", outputName, "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"); err != nil {
		return ProtectedRecording{}, fmt.Errorf("verify protected recording: %w", err)
	}
	info, err := os.Stat(outputName)
	if err != nil {
		return ProtectedRecording{}, err
	}
	if info.Size() == 0 {
		return ProtectedRecording{}, errors.New("protected recording is empty")
	}
	file, err := os.OpenFile(outputName, os.O_RDWR, 0)
	if err != nil {
		return ProtectedRecording{}, err
	}
	err = file.Sync()
	closeErr := file.Close()
	if err != nil {
		return ProtectedRecording{}, err
	}
	if closeErr != nil {
		return ProtectedRecording{}, closeErr
	}
	if _, err := manager.protectedHealth(); err != nil {
		return ProtectedRecording{}, err
	}
	filename := recording.ID + ".mp4"
	finalName := filepath.Join(manager.protectedDir, filename)
	recording.CoveredStart, recording.CoveredEnd = start, end
	recording.Size, recording.Filename, recording.Status, recording.Gaps = info.Size(), filename, "saving", gaps
	manager.protectedMu.Lock()
	defer manager.protectedMu.Unlock()
	recordings, err := manager.protectedRecordings()
	if err != nil {
		return ProtectedRecording{}, err
	}
	recordings = append(recordings, recording)
	if err := manager.saveProtectedIndex(recordings); err != nil {
		return ProtectedRecording{}, err
	}
	if err := os.Link(outputName, finalName); err != nil {
		return ProtectedRecording{}, err
	}
	if err := syncProtectedDirectory(manager.protectedDir); err != nil {
		return ProtectedRecording{}, err
	}
	recording.Status = "saved"
	recordings[len(recordings)-1] = recording
	if err := manager.saveProtectedIndex(recordings); err != nil {
		return ProtectedRecording{}, err
	}
	return recording, nil
}

func (manager *recordingManager) runProtectedFFmpeg(ctx context.Context, ffmpeg string, args ...string) error {
	if _, err := manager.protectedHealth(); err != nil {
		return err
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	cmd := exec.CommandContext(ctx, ffmpeg, args...)
	var output bytes.Buffer
	cmd.Stderr = &output
	if err := cmd.Start(); err != nil {
		return err
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case err := <-done:
			if err != nil {
				return fmt.Errorf("%w: %s", err, strings.TrimSpace(output.String()))
			}
			_, err = manager.protectedHealth()
			return err
		case <-ctx.Done():
			cancel()
			<-done
			return ctx.Err()
		case <-ticker.C:
			if _, err := manager.protectedHealth(); err != nil {
				cancel()
				<-done
				return err
			}
		}
	}
}

func (manager *recordingManager) cleanupLoop() {
	ticker := time.NewTicker(time.Hour)
	defer ticker.Stop()
	manager.cleanup()
	for {
		select {
		case <-manager.ctx.Done():
			return
		case <-ticker.C:
			manager.cleanup()
		}
	}
}

func (manager *recordingManager) cleanup() {
	if !manager.enabled() {
		return
	}
	manager.retentionMu.Lock()
	defer manager.retentionMu.Unlock()
	protectedInfo, err := os.Stat(manager.protectedDir)
	if err != nil {
		return
	}
	root, err := filepath.Abs(manager.dir)
	if err != nil {
		return
	}
	for path := root; ; path = filepath.Dir(path) {
		info, err := os.Stat(path)
		if err != nil || os.SameFile(info, protectedInfo) {
			return
		}
		if filepath.Dir(path) == path {
			break
		}
	}
	settings := manager.recordingSettings()
	retention, _ := time.ParseDuration(settings.Retention)

	cutoff := time.Now().Add(-retention)
	var files []string
	var total int64
	_ = filepath.WalkDir(manager.dir, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if entry.IsDir() {
			info, err := os.Stat(path)
			if err != nil || os.SameFile(info, protectedInfo) {
				return filepath.SkipDir
			}
			return nil
		}
		if filepath.Base(path) == ".netvue-recordings.json" {
			return nil
		}
		extension := strings.ToLower(filepath.Ext(path))
		if extension != ".mp4" && extension != ".tmp" {
			return nil
		}
		info, err := entry.Info()
		if err == nil {
			total += info.Size()
			files = append(files, path)
			if info.ModTime().Before(cutoff) {
				if os.Remove(path) == nil {
					total -= info.Size()
				}
			}
		}
		return nil
	})

	limit := settings.LimitBytes
	if limit <= 0 {
		return
	}
	sort.Slice(files, func(i, j int) bool {
		infoI, _ := os.Stat(files[i])
		infoJ, _ := os.Stat(files[j])
		if infoI == nil || infoJ == nil {
			return false
		}
		return infoI.ModTime().Before(infoJ.ModTime())
	})
	for _, path := range files {
		if total <= limit {
			break
		}
		info, err := os.Stat(path)
		if err != nil {
			continue
		}
		if os.Remove(path) == nil {
			total -= info.Size()
		}
	}
}
