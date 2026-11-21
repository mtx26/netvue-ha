package manager

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
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

type recordingState struct {
	Manual     bool `json:"manual"`
	Continuous bool `json:"continuous"`
}

type recordingConfig struct {
	Cameras map[string]recordingState `json:"cameras"`
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
	controlURL      string
	playbackAddress string
	client          *http.Client
	playbackSlots   chan struct{}

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
		states:          make(map[string]recordingState),
	}
	if dir == "" {
		return manager
	}
	if err := os.MkdirAll(dir, 0o750); err == nil {
		manager.load()
		go manager.cleanupLoop()
	}
	return manager
}

func (manager *recordingManager) enabled() bool {
	return manager != nil && manager.dir != ""
}

func (manager *recordingManager) configPath(streamName string) string {
	return filepath.Join(manager.dir, ".netvue-recordings.json")
}

func (manager *recordingManager) load() {
	manager.mu.Lock()
	defer manager.mu.Unlock()
	data, err := os.ReadFile(manager.configPath(""))
	if err != nil {
		if !errors.Is(err, os.ErrNotExist) {
			return
		}
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
	temp, err := os.CreateTemp(manager.dir, ".netvue-recordings-*.json")
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
	return os.Rename(tempName, manager.configPath(""))
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
	query.Set("duration", fmt.Sprintf("%.3f", segment.Duration))
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
	retentionText := strings.TrimSpace(os.Getenv("NETVUE_RECORDING_RETENTION"))
	retention, err := time.ParseDuration(retentionText)
	if err != nil || retention <= 0 {
		retention = 7 * 24 * time.Hour
	}
	cutoff := time.Now().Add(-retention)
	var files []string
	var total int64
	_ = filepath.WalkDir(manager.dir, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if entry.IsDir() {
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
				_ = os.Remove(path)
				total -= info.Size()
			}
		}
		return nil
	})

	limitText := strings.TrimSpace(os.Getenv("NETVUE_RECORDING_LIMIT_BYTES"))
	var limit int64
	if limitText != "" {
		fmt.Sscanf(limitText, "%d", &limit)
	}
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
