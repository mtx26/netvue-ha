package manager

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	defaultAIMetadataLimit = 10000
	maxAIMetadataEvents    = 10000
	maxAIMetadataLineBytes = 16 * 1024
	maxAIMetadataBoxes     = 32
	aiMetadataFilename     = ".netvue-ai-metadata.jsonl"
)

type AIDetectionBox struct {
	X1         float64 `json:"x1"`
	Y1         float64 `json:"y1"`
	X2         float64 `json:"x2"`
	Y2         float64 `json:"y2"`
	Category   string  `json:"category,omitempty"`
	Confidence float64 `json:"confidence,omitempty"`
}

type AIDetectionMetadata struct {
	ID          string           `json:"id"`
	Timestamp   float64          `json:"timestamp"`
	StreamName  string           `json:"stream_name"`
	Event       string           `json:"event,omitempty"`
	Category    string           `json:"category"`
	Confidence  float64          `json:"confidence"`
	TrackID     int              `json:"track_id"`
	Zone        string           `json:"zone,omitempty"`
	FrameWidth  int              `json:"frame_width"`
	FrameHeight int              `json:"frame_height"`
	Boxes       []AIDetectionBox `json:"boxes"`
}

type aiMetadataStore struct {
	mu              sync.RWMutex
	path            string
	maxEvents       int
	retention       time.Duration
	events          []AIDetectionMetadata
	ids             map[string]struct{}
	needsCompaction bool
}

func newAIMetadataStore(dir string) (*aiMetadataStore, error) {
	maxEvents := defaultAIMetadataLimit
	if value, err := strconv.Atoi(strings.TrimSpace(
		os.Getenv("NETVUE_AI_METADATA_MAX_EVENTS"),
	)); err == nil && value > 0 && value <= maxAIMetadataEvents {
		maxEvents = value
	}
	retention, err := time.ParseDuration(strings.TrimSpace(os.Getenv("NETVUE_RECORDING_RETENTION")))
	if err != nil || retention <= 0 {
		retention = 7 * 24 * time.Hour
	}
	return newAIMetadataStoreWithLimits(dir, maxEvents, retention)
}

func newAIMetadataStoreWithLimits(dir string, maxEvents int, retention time.Duration) (*aiMetadataStore, error) {
	if dir == "" {
		return nil, nil
	}
	if maxEvents < 1 || retention <= 0 {
		return nil, errors.New("invalid AI metadata storage limits")
	}
	store := &aiMetadataStore{
		path:      filepath.Join(dir, aiMetadataFilename),
		maxEvents: maxEvents,
		retention: retention,
		ids:       make(map[string]struct{}),
	}
	if err := store.load(); err != nil {
		return nil, err
	}
	return store, nil
}

func (store *aiMetadataStore) load() error {
	file, err := os.Open(store.path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("open AI metadata index: %w", err)
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 4096), maxAIMetadataLineBytes)
	dirty := false
	for scanner.Scan() {
		var event AIDetectionMetadata
		if err := json.Unmarshal(scanner.Bytes(), &event); err != nil || validateAIMetadata(event) != nil {
			dirty = true
			continue
		}
		if _, duplicate := store.ids[aiMetadataIDKey(event)]; duplicate {
			dirty = true
			continue
		}
		store.events = append(store.events, event)
		store.ids[aiMetadataIDKey(event)] = struct{}{}
	}
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("read AI metadata index: %w", err)
	}
	sortAIMetadata(store.events)
	count := len(store.events)
	store.pruneLocked(time.Now())
	store.needsCompaction = dirty || count != len(store.events)
	return nil
}

func (store *aiMetadataStore) add(event AIDetectionMetadata) (bool, error) {
	if err := validateAIMetadata(event); err != nil {
		return false, err
	}
	store.mu.Lock()
	defer store.mu.Unlock()
	if _, exists := store.ids[aiMetadataIDKey(event)]; exists {
		return false, nil
	}

	now := time.Now()
	candidate := make([]AIDetectionMetadata, 0, len(store.events)+1)
	cutoff := now.Add(-store.retention).UnixNano()
	for _, current := range store.events {
		if timestampNanos(current.Timestamp) >= cutoff {
			candidate = append(candidate, current)
		}
	}
	candidate = append(candidate, event)
	sortAIMetadata(candidate)
	if len(candidate) > store.maxEvents {
		candidate = candidate[len(candidate)-store.maxEvents:]
	}
	eventRetained := false
	for _, current := range candidate {
		if aiMetadataIDKey(current) == aiMetadataIDKey(event) {
			eventRetained = true
			break
		}
	}
	if !eventRetained {
		return false, nil
	}

	compacted := store.needsCompaction || len(candidate) != len(store.events)+1
	if compacted {
		if err := store.writeSnapshot(candidate); err != nil {
			return false, err
		}
		store.needsCompaction = false
	} else if err := store.append(event); err != nil {
		return false, err
	}

	store.events = candidate
	store.ids = make(map[string]struct{}, len(candidate))
	for _, current := range candidate {
		store.ids[aiMetadataIDKey(current)] = struct{}{}
	}
	return true, nil
}

func (store *aiMetadataStore) between(streamName string, start, end time.Time) []AIDetectionMetadata {
	store.mu.RLock()
	defer store.mu.RUnlock()
	first := 0
	if !start.IsZero() {
		startTimestamp := float64(start.UnixNano()) / float64(time.Second)
		first = sort.Search(len(store.events), func(i int) bool {
			return store.events[i].Timestamp >= startTimestamp
		})
	}
	last := len(store.events)
	if !end.IsZero() {
		endTimestamp := float64(end.UnixNano()) / float64(time.Second)
		last = sort.Search(len(store.events), func(i int) bool {
			return store.events[i].Timestamp >= endTimestamp
		})
	}
	if first >= last {
		return []AIDetectionMetadata{}
	}
	result := make([]AIDetectionMetadata, 0, last-first)
	for _, event := range store.events[first:last] {
		if event.StreamName == streamName {
			result = append(result, event)
		}
	}
	return result
}

func (store *aiMetadataStore) prune(now time.Time) {
	store.mu.Lock()
	defer store.mu.Unlock()
	if store.pruneLocked(now) {
		store.needsCompaction = true
	}
	if store.needsCompaction && store.writeSnapshot(store.events) == nil {
		store.needsCompaction = false
	}
}

func (store *aiMetadataStore) pruneLocked(now time.Time) bool {
	cutoff := now.Add(-store.retention).UnixNano()
	first := sort.Search(len(store.events), func(i int) bool {
		return timestampNanos(store.events[i].Timestamp) >= cutoff
	})
	last := len(store.events)
	if last-first > store.maxEvents {
		first = last - store.maxEvents
	}
	if first == 0 {
		return false
	}
	store.events = append([]AIDetectionMetadata(nil), store.events[first:]...)
	store.ids = make(map[string]struct{}, len(store.events))
	for _, event := range store.events {
		store.ids[aiMetadataIDKey(event)] = struct{}{}
	}
	return true
}

func (store *aiMetadataStore) append(event AIDetectionMetadata) error {
	data, err := json.Marshal(event)
	if err != nil {
		return err
	}
	file, err := os.OpenFile(store.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return fmt.Errorf("append AI metadata: %w", err)
	}
	if _, err := file.Write(append(data, '\n')); err != nil {
		file.Close()
		return fmt.Errorf("append AI metadata: %w", err)
	}
	if err := file.Sync(); err != nil {
		file.Close()
		return fmt.Errorf("sync AI metadata: %w", err)
	}
	if err := file.Close(); err != nil {
		return fmt.Errorf("close AI metadata index: %w", err)
	}
	return nil
}

func (store *aiMetadataStore) writeSnapshot(events []AIDetectionMetadata) error {
	file, err := os.CreateTemp(filepath.Dir(store.path), ".netvue-ai-metadata-*.snapshot")
	if err != nil {
		return fmt.Errorf("create AI metadata snapshot: %w", err)
	}
	path := file.Name()
	defer os.Remove(path)
	if err := file.Chmod(0o600); err != nil {
		file.Close()
		return err
	}
	writer := bufio.NewWriter(file)
	for _, event := range events {
		data, err := json.Marshal(event)
		if err != nil {
			file.Close()
			return err
		}
		if _, err := writer.Write(append(data, '\n')); err != nil {
			file.Close()
			return err
		}
	}
	if err := writer.Flush(); err != nil {
		file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := os.Rename(path, store.path); err != nil {
		return fmt.Errorf("replace AI metadata index: %w", err)
	}
	return nil
}

func validateAIMetadata(event AIDetectionMetadata) error {
	if strings.TrimSpace(event.ID) == "" || strings.TrimSpace(event.StreamName) == "" {
		return errors.New("AI metadata requires an id and stream name")
	}
	if math.IsNaN(event.Timestamp) || math.IsInf(event.Timestamp, 0) || event.Timestamp <= 0 ||
		event.Timestamp > float64(math.MaxInt64)/float64(time.Second) {
		return errors.New("AI metadata has an invalid timestamp")
	}
	if event.FrameWidth < 1 || event.FrameHeight < 1 ||
		event.FrameWidth > 32768 || event.FrameHeight > 32768 {
		return errors.New("AI metadata has invalid frame dimensions")
	}
	if event.Event == "object_cleared" {
		if len(event.Boxes) != 0 || event.TrackID != 0 || event.Category != "" ||
			event.Confidence != 0 {
			return errors.New("AI clear metadata must not contain a detection")
		}
		return nil
	}
	if event.TrackID < 1 {
		return errors.New("AI metadata has invalid track id")
	}
	if len(event.Boxes) == 0 || len(event.Boxes) > maxAIMetadataBoxes {
		return errors.New("AI metadata must contain a bounded set of boxes")
	}
	for _, box := range event.Boxes {
		if !finite(box.X1) || !finite(box.Y1) || !finite(box.X2) || !finite(box.Y2) ||
			box.X1 < 0 || box.Y1 < 0 || box.X2 > float64(event.FrameWidth) ||
			box.Y2 > float64(event.FrameHeight) || box.X2 <= box.X1 || box.Y2 <= box.Y1 {
			return errors.New("AI metadata contains an invalid box")
		}
		if box.Confidence < 0 || box.Confidence > 1 || math.IsNaN(box.Confidence) ||
			math.IsInf(box.Confidence, 0) {
			return errors.New("AI metadata contains invalid box confidence")
		}
	}
	if event.Confidence < 0 || event.Confidence > 1 ||
		math.IsNaN(event.Confidence) || math.IsInf(event.Confidence, 0) {
		return errors.New("AI metadata has invalid confidence")
	}
	return nil
}

func timestampNanos(timestamp float64) int64 {
	return int64(timestamp * float64(time.Second))
}

func sortAIMetadata(events []AIDetectionMetadata) {
	sort.Slice(events, func(i, j int) bool {
		if events[i].Timestamp == events[j].Timestamp {
			if events[i].StreamName == events[j].StreamName {
				return events[i].ID < events[j].ID
			}
			return events[i].StreamName < events[j].StreamName
		}
		return events[i].Timestamp < events[j].Timestamp
	})
}

func aiMetadataIDKey(event AIDetectionMetadata) string {
	return event.StreamName + "\x00" + event.ID
}

func finite(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}
