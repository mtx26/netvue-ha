package manager

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"sort"
	"sync"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue"
)

const (
	defaultRTSPAddress = "127.0.0.1:8554"
	stateSupervising   = "supervising"
	stateStopping      = "stopping"
	stateStopped       = "stopped"
	stateFailed        = "failed"
)

var (
	ErrInvalidCamera  = errors.New("invalid camera configuration")
	ErrCameraConflict = errors.New("camera or stream already exists")
	ErrCameraNotFound = errors.New("camera not found")
	ErrManagerClosed  = errors.New("camera manager is shutting down")
)

type Camera struct {
	Host           string                    `json:"host"`
	StreamName     string                    `json:"stream_name"`
	RTSPURL        string                    `json:"rtsp_url"`
	State          string                    `json:"state"`
	Error          string                    `json:"error,omitempty"`
	ConnectionPort int                       `json:"connection_port,omitempty"`
	Metadata       *netvue.DiscoveryMetadata `json:"metadata,omitempty"`
}

type cameraSession struct {
	Camera
	// Serialize lifecycle operations; recheck manager.cameras after acquiring it
	// because the session may have been deleted while waiting.
	operationMu sync.Mutex
	cancel      context.CancelFunc
	done        chan struct{}
	talkback    *netvue.LiveTalkback
}

type sessionRunner func(context.Context, string, string, string) error

type Manager struct {
	mu            sync.RWMutex
	discoveryMu   sync.Mutex
	ctx           context.Context
	cancel        context.CancelFunc
	source        string
	runSession    sessionRunner
	runWithTalk   func(context.Context, string, string, string, *netvue.LiveTalkback) error
	openAudio     func(context.Context, string) (talkSink, error)
	ffmpegPath    func() (string, error)
	adaptVideo    func(context.Context, string, string, rtpTrackWriter) error
	adapters      map[string]*webRTCVideoAdapter
	turn          *cloudflareTURN
	discoverLAN   func(context.Context, string) ([]netvue.DiscoveryResult, error)
	discovered    map[string]netvue.DiscoveryResult
	cameras       map[string]*cameraSession
	peers         map[string]*webRTCPeer
	tombstones    map[string]time.Time
	closed        bool
	closeOnce     sync.Once
	closeComplete chan struct{}
	wg            sync.WaitGroup
}

func New(ctx context.Context, source string) (*Manager, error) {
	manager, err := newManager(ctx, source, netvue.RunContinuous)
	if err == nil {
		manager.runSession = func(ctx context.Context, source, target, rtspURL string) error {
			return netvue.RunContinuousWithDiscovery(
				ctx, source, target, rtspURL, manager.updateDiscoveryMetadata,
			)
		}
		manager.runWithTalk = func(
			ctx context.Context,
			source, target, rtspURL string,
			live *netvue.LiveTalkback,
		) error {
			return netvue.RunContinuousWithTalkbackAndDiscovery(
				ctx, source, target, rtspURL, live, manager.updateDiscoveryMetadata,
			)
		}
	}
	return manager, err
}

func newManager(
	ctx context.Context,
	source string,
	runSession sessionRunner,
) (*Manager, error) {
	if ctx == nil || runSession == nil {
		return nil, fmt.Errorf("%w: missing context or session runner", ErrInvalidCamera)
	}
	sourceIP := net.ParseIP(source)
	if sourceIP == nil || sourceIP.To4() == nil {
		return nil, fmt.Errorf("%w: source address must be IPv4", ErrInvalidCamera)
	}
	if err := netvue.ValidateLANIPv4(sourceIP.String()); err != nil {
		return nil, fmt.Errorf("%w: invalid source address: %v", ErrInvalidCamera, err)
	}
	source = sourceIP.To4().String()

	managerCtx, cancel := context.WithCancel(ctx)
	return &Manager{
		ctx:           managerCtx,
		cancel:        cancel,
		source:        source,
		runSession:    runSession,
		discoverLAN:   netvue.DiscoverLANMetadata,
		discovered:    make(map[string]netvue.DiscoveryResult),
		cameras:       make(map[string]*cameraSession),
		peers:         make(map[string]*webRTCPeer),
		adaptVideo:    transcodeH264ToWebRTC,
		adapters:      make(map[string]*webRTCVideoAdapter),
		tombstones:    make(map[string]time.Time),
		closeComplete: make(chan struct{}),
	}, nil
}

// AddCamera registers a camera. added is false for an existing identical camera.
func (manager *Manager) AddCamera(host, streamName string) (Camera, bool, error) {
	host, err := normalizeCameraHost(host)
	if err != nil {
		return Camera{}, false, err
	}
	if host == manager.source {
		return Camera{}, false, fmt.Errorf("%w: camera and source addresses must differ", ErrInvalidCamera)
	}
	if !validStreamName(streamName) {
		return Camera{}, false, fmt.Errorf("%w: invalid RTSP stream name", ErrInvalidCamera)
	}
	rtspURL := "rtsp://" + defaultRTSPAddress + "/" + streamName

	manager.mu.Lock()
	defer manager.mu.Unlock()
	if manager.closed || manager.ctx.Err() != nil {
		return Camera{}, false, ErrManagerClosed
	}
	if existing := manager.cameras[host]; existing != nil {
		if existing.StreamName != streamName {
			return Camera{}, false, ErrCameraConflict
		}
		return existing.Camera, false, nil
	}
	for _, existing := range manager.cameras {
		if existing.StreamName == streamName {
			return Camera{}, false, ErrCameraConflict
		}
	}

	session := &cameraSession{Camera: Camera{
		Host: host, StreamName: streamName, RTSPURL: rtspURL, State: stateSupervising,
	}, talkback: netvue.NewLiveTalkback()}
	discovery, discovered := manager.discovered[host]
	if discovered {
		manager.applyDiscoveryResultLocked(session, discovery)
	}
	manager.cameras[host] = session
	manager.startLocked(session)
	return session.Camera, true, nil
}

// OpenTalkback opens an exclusive experimental ADTS stream on the current
// supervised session. It never creates or reconnects a camera session.
func (manager *Manager) OpenTalkback(ctx context.Context, host string) (*netvue.TalkStream, error) {
	if ctx == nil {
		return nil, errors.New("request context is required")
	}
	host, err := normalizeCameraHost(host)
	if err != nil {
		return nil, err
	}
	manager.mu.RLock()
	session := manager.cameras[host]
	closed := manager.closed || manager.ctx.Err() != nil
	if closed {
		manager.mu.RUnlock()
		return nil, ErrManagerClosed
	}
	if session == nil {
		manager.mu.RUnlock()
		return nil, ErrCameraNotFound
	}
	manager.mu.RUnlock()
	if err := lockCameraOperation(ctx, &session.operationMu); err != nil {
		return nil, err
	}
	defer session.operationMu.Unlock()
	manager.mu.RLock()
	if manager.closed || manager.ctx.Err() != nil {
		manager.mu.RUnlock()
		return nil, ErrManagerClosed
	}
	if manager.cameras[host] != session {
		manager.mu.RUnlock()
		return nil, ErrCameraNotFound
	}
	if session.State != stateSupervising {
		manager.mu.RUnlock()
		return nil, netvue.ErrTalkUnavailable
	}
	controller := session.talkback
	manager.mu.RUnlock()
	return controller.Open(ctx)
}

// StartCamera starts a stopped camera; started is false if already supervising.
func (manager *Manager) StartCamera(host string) (Camera, bool, error) {
	host, err := normalizeCameraHost(host)
	if err != nil {
		return Camera{}, false, err
	}
	manager.mu.RLock()
	session := manager.cameras[host]
	closed := manager.closed || manager.ctx.Err() != nil
	manager.mu.RUnlock()
	if closed {
		return Camera{}, false, ErrManagerClosed
	}
	if session == nil {
		return Camera{}, false, ErrCameraNotFound
	}
	session.operationMu.Lock()
	defer session.operationMu.Unlock()

	manager.mu.Lock()
	defer manager.mu.Unlock()
	if manager.closed || manager.ctx.Err() != nil {
		return Camera{}, false, ErrManagerClosed
	}
	if manager.cameras[host] != session {
		return Camera{}, false, ErrCameraNotFound
	}
	switch session.State {
	case stateSupervising:
		return session.Camera, false, nil
	case stateStopping:
		return Camera{}, false, ErrCameraConflict
	default:
		session.State = stateSupervising
		session.Error = ""
		manager.startLocked(session)
		return session.Camera, true, nil
	}
}

func (manager *Manager) StopCamera(ctx context.Context, host string) (Camera, error) {
	camera, _, err := manager.stopSession(ctx, host, false)
	if err != nil {
		return Camera{}, err
	}
	return camera, nil
}

// DeleteCamera stops and removes a camera. Deleting an absent camera succeeds
// with deleted false.
func (manager *Manager) DeleteCamera(ctx context.Context, host string) (bool, error) {
	_, deleted, err := manager.stopSession(ctx, host, true)
	if errors.Is(err, ErrCameraNotFound) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return deleted, nil
}

func (manager *Manager) Camera(host string) (Camera, bool) {
	host, err := normalizeCameraHost(host)
	if err != nil {
		return Camera{}, false
	}
	manager.mu.RLock()
	defer manager.mu.RUnlock()
	session := manager.cameras[host]
	if session == nil {
		return Camera{}, false
	}
	return session.Camera, true
}

func (manager *Manager) Cameras() []Camera {
	manager.mu.RLock()
	cameras := make([]Camera, 0, len(manager.cameras))
	for _, session := range manager.cameras {
		cameras = append(cameras, session.Camera)
	}
	manager.mu.RUnlock()
	sort.Slice(cameras, func(i, j int) bool {
		return cameras[i].Host < cameras[j].Host
	})
	return cameras
}

// Close begins shutdown once. If ctx expires, shutdown continues until workers exit.
func (manager *Manager) Close(ctx context.Context) error {
	if ctx == nil {
		return errors.New("shutdown context is required")
	}
	manager.closeOnce.Do(func() {
		manager.mu.Lock()
		manager.closed = true
		manager.cancel()
		for _, session := range manager.cameras {
			if session.State == stateSupervising {
				session.State = stateStopping
			}
		}
		manager.mu.Unlock()
		go func() {
			manager.wg.Wait()
			close(manager.closeComplete)
		}()
	})
	select {
	case <-manager.closeComplete:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (manager *Manager) stopSession(ctx context.Context, host string, remove bool) (Camera, bool, error) {
	if ctx == nil {
		return Camera{}, false, errors.New("request context is required")
	}
	host, err := normalizeCameraHost(host)
	if err != nil {
		return Camera{}, false, err
	}
	manager.mu.RLock()
	session := manager.cameras[host]
	manager.mu.RUnlock()
	if session == nil {
		return Camera{}, false, ErrCameraNotFound
	}
	if err := lockCameraOperation(ctx, &session.operationMu); err != nil {
		return Camera{}, false, err
	}
	defer session.operationMu.Unlock()

	manager.mu.Lock()
	if manager.cameras[host] != session {
		manager.mu.Unlock()
		return Camera{}, false, ErrCameraNotFound
	}
	if session.State != stateStopped {
		session.State = stateStopping
		session.cancel()
	}
	done := session.done
	manager.mu.Unlock()

	peers := manager.hostPeers(host)
	for _, peer := range peers {
		peer.fail("camera_stopped")
	}
	for _, peer := range peers {
		if err := waitPeerWorkers(ctx, peer); err != nil {
			return Camera{}, false, err
		}
	}
	select {
	case <-done:
	case <-ctx.Done():
		return Camera{}, false, ctx.Err()
	}

	manager.mu.Lock()
	defer manager.mu.Unlock()
	if manager.cameras[host] != session {
		return Camera{}, false, ErrCameraNotFound
	}
	session.State = stateStopped
	session.Error = ""
	if remove {
		delete(manager.cameras, host)
		return session.Camera, true, nil
	}
	return session.Camera, false, nil
}

func lockCameraOperation(ctx context.Context, mu *sync.Mutex) error {
	for !mu.TryLock() {
		timer := time.NewTimer(10 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
	if err := ctx.Err(); err != nil {
		mu.Unlock()
		return err
	}
	return nil
}

func (manager *Manager) startLocked(session *cameraSession) {
	ctx, cancel := context.WithCancel(manager.ctx)
	done := make(chan struct{})
	session.cancel = cancel
	session.done = done
	manager.wg.Add(1)
	go manager.run(ctx, session, done)
}

func (manager *Manager) applyDiscoveryResultLocked(
	session *cameraSession,
	result netvue.DiscoveryResult,
) {
	session.ConnectionPort = result.ConnectionPort
	session.Metadata = mergeDiscoveryMetadata(session.Metadata, result.Metadata)
}

func (manager *Manager) updateDiscoveryMetadata(result netvue.DiscoveryResult) {
	host, err := normalizeCameraHost(result.Host)
	if err != nil || host != result.Host ||
		result.ConnectionPort < 1 || result.ConnectionPort > 65535 {
		slog.Warn("Ignoring invalid camera metadata from discovery")
		return
	}

	manager.mu.Lock()
	defer manager.mu.Unlock()
	if manager.closed {
		return
	}
	session := manager.cameras[host]
	if session == nil {
		return
	}
	manager.applyDiscoveryResultLocked(session, result)
	manager.discovered[host] = cloneDiscoveryResult(result)
}

func (manager *Manager) cacheDiscoveryResults(
	results []netvue.DiscoveryResult,
) ([]netvue.DiscoveryResult, error) {
	discovered := make(map[string]netvue.DiscoveryResult, len(results))
	for _, result := range results {
		host, err := normalizeCameraHost(result.Host)
		if err != nil || host != result.Host ||
			result.ConnectionPort < 1 || result.ConnectionPort > 65535 {
			return nil, fmt.Errorf("%w: invalid discovery result", ErrInvalidCamera)
		}
		if _, exists := discovered[host]; exists {
			continue
		}
		discovered[host] = cloneDiscoveryResult(result)
	}

	cameras := make([]netvue.DiscoveryResult, 0, len(discovered))
	for _, result := range discovered {
		cameras = append(cameras, result)
	}
	sort.Slice(cameras, func(i, j int) bool {
		return cameras[i].Host < cameras[j].Host
	})

	manager.mu.Lock()
	defer manager.mu.Unlock()
	if manager.closed || manager.ctx.Err() != nil {
		return nil, ErrManagerClosed
	}
	manager.discovered = discovered
	for _, result := range cameras {
		if session := manager.cameras[result.Host]; session != nil {
			manager.applyDiscoveryResultLocked(session, result)
		}
	}
	return cameras, nil
}

func cloneDiscoveryResult(result netvue.DiscoveryResult) netvue.DiscoveryResult {
	result.Metadata = cloneDiscoveryMetadata(result.Metadata)
	return result
}

func cloneDiscoveryMetadata(metadata *netvue.DiscoveryMetadata) *netvue.DiscoveryMetadata {
	if metadata == nil {
		return nil
	}
	cloned := *metadata
	if metadata.WiFiSignalLevel != nil {
		signal := *metadata.WiFiSignalLevel
		cloned.WiFiSignalLevel = &signal
	}
	if metadata.Capabilities != nil {
		capabilities := *metadata.Capabilities
		if metadata.Capabilities.Audio != nil {
			value := *metadata.Capabilities.Audio
			capabilities.Audio = &value
		}
		if metadata.Capabilities.Flip != nil {
			value := *metadata.Capabilities.Flip
			capabilities.Flip = &value
		}
		if metadata.Capabilities.Light != nil {
			value := *metadata.Capabilities.Light
			capabilities.Light = &value
		}
		if metadata.Capabilities.SDCard != nil {
			value := *metadata.Capabilities.SDCard
			capabilities.SDCard = &value
		}
		cloned.Capabilities = &capabilities
	}
	return &cloned
}

func mergeDiscoveryMetadata(
	current, next *netvue.DiscoveryMetadata,
) *netvue.DiscoveryMetadata {
	merged := cloneDiscoveryMetadata(current)
	if next == nil {
		if merged != nil {
			merged.WiFiSSID = ""
			merged.WiFiSignalLevel = nil
			merged.Capabilities = nil
		}
		return merged
	}
	if merged == nil {
		merged = &netvue.DiscoveryMetadata{}
	}
	next = cloneDiscoveryMetadata(next)
	if next.SerialNumber != "" {
		merged.SerialNumber = next.SerialNumber
	}
	if next.FirmwareVersion != "" {
		merged.FirmwareVersion = next.FirmwareVersion
	}
	merged.WiFiSSID = next.WiFiSSID
	merged.WiFiSignalLevel = next.WiFiSignalLevel
	merged.Capabilities = next.Capabilities
	return merged
}

func (manager *Manager) run(ctx context.Context, session *cameraSession, done chan struct{}) {
	var err error
	if manager.runWithTalk != nil {
		err = manager.runWithTalk(ctx, manager.source, session.Host, session.RTSPURL, session.talkback)
	} else {
		err = manager.runSession(ctx, manager.source, session.Host, session.RTSPURL)
	}
	canceled := ctx.Err() != nil
	session.cancel()
	var diagnostic string
	if err != nil && !canceled {
		diagnostic = netvue.SessionDiagnostic(err)
		slog.Error("Camera supervisor stopped", "camera", session.Host, "error", diagnostic)
	}

	manager.mu.Lock()
	if manager.cameras[session.Host] == session && session.done == done {
		switch {
		case canceled:
			session.State = stateStopped
			session.Error = ""
		case err != nil:
			session.State = stateFailed
			session.Error = diagnostic
		default:
			session.State = stateStopped
			session.Error = ""
		}
	}
	close(done)
	manager.mu.Unlock()
	manager.wg.Done()
}

func normalizeCameraHost(host string) (string, error) {
	ip := net.ParseIP(host)
	if ip == nil || ip.To4() == nil {
		return "", fmt.Errorf("%w: camera address must be IPv4", ErrInvalidCamera)
	}
	if err := netvue.ValidateLANIPv4(ip.String()); err != nil {
		return "", fmt.Errorf("%w: invalid camera address: %v", ErrInvalidCamera, err)
	}
	return ip.To4().String(), nil
}

func validStreamName(name string) bool {
	if len(name) == 0 || len(name) > 128 {
		return false
	}
	for _, char := range name {
		if (char < 'a' || char > 'z') &&
			(char < 'A' || char > 'Z') &&
			(char < '0' || char > '9') &&
			char != '_' && char != '-' {
			return false
		}
	}
	return true
}
