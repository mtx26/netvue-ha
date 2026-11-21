package manager

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type protectedStorageHealth struct {
	FreeBytes    uint64 `json:"free_bytes"`
	MinFreeBytes uint64 `json:"min_free_bytes"`
	Healthy      bool   `json:"healthy"`
}

func (manager *recordingManager) checkProtectedIndex() error {
	manager.protectedMu.Lock()
	defer manager.protectedMu.Unlock()
	recordings, err := manager.protectedRecordings()
	if err != nil {
		return err
	}
	for _, recording := range recordings {
		if recording.Status == "saving" {
			return errors.New("protected recording publication is incomplete")
		}
		if recording.Status != "saved" {
			continue
		}
		if recording.Filename != recording.ID+".mp4" || filepath.Base(recording.Filename) != recording.Filename {
			return errors.New("invalid protected recording filename")
		}
		info, err := os.Lstat(filepath.Join(manager.protectedDir, recording.Filename))
		if err != nil || !info.Mode().IsRegular() || info.Size() != recording.Size {
			return errors.New("protected recording file is missing or has an unexpected size")
		}
	}
	return nil
}

func protectedMaxDuration() (time.Duration, error) {
	value := strings.TrimSpace(os.Getenv("NETVUE_PROTECTED_MAX_DURATION"))
	if value == "" {
		return time.Hour, nil
	}
	duration, err := time.ParseDuration(value)
	if err != nil || duration <= 0 {
		return 0, errors.New("invalid protected recording maximum duration")
	}
	return duration, nil
}

func (manager *recordingManager) protectedHealth() (protectedStorageHealth, error) {
	var health protectedStorageHealth
	if !manager.protectedEnabled() {
		return health, errors.New("protected recording storage is not configured")
	}
	health.MinFreeBytes = 1 << 30
	if value := strings.TrimSpace(os.Getenv("NETVUE_PROTECTED_MIN_FREE_BYTES")); value != "" {
		parsed, err := strconv.ParseUint(value, 10, 64)
		if err != nil {
			return health, errors.New("invalid protected storage free space threshold")
		}
		health.MinFreeBytes = parsed
	}
	free, err := protectedFreeBytes(manager.protectedDir)
	if err != nil {
		return health, fmt.Errorf("check protected storage space: %w", err)
	}
	health.FreeBytes = free
	health.Healthy = free >= health.MinFreeBytes
	if !health.Healthy {
		return health, errors.New("protected storage free space is below threshold")
	}
	return health, nil
}
