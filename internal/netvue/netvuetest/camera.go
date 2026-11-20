package netvuetest

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

const (
	headerSize    = 16
	maxBody       = 1 << 20
	kindCommand   = 200
	kindMedia     = 201
	discoveryPort = 36868
	// DefaultKey is the local key announced by discovery and expected at login.
	DefaultKey = "synthetic-key"
)

// Options configures one simulated camera. The zero value plus Host is valid.
// Video and AudioFrames are retained without copying and must not be mutated
// while in use; a supplied Video sequence must be non-empty.
type Options struct {
	// Host is the loopback or private IPv4 address of this camera (required).
	Host string
	// DiscoveryBind is the UDP address to bind; empty means Host. A container
	// camera answering broadcasts binds 0.0.0.0.
	DiscoveryBind string
	// DiscoveryPort defaults to 36868, the port the bridge uses.
	DiscoveryPort int
	// TCPPort defaults to an ephemeral port.
	TCPPort int
	// Video is the looping H.264 sequence; a supplied sequence must be non-empty
	// and is retained without copying.
	Video []Frame
	// FrameInterval is the video cadence; defaults to 100ms.
	FrameInterval time.Duration
	// Audio advertises an audio channel and streams AudioFrames (ADTS).
	Audio       bool
	AudioFrames [][]byte
	AudioRate   int // defaults to 8000
	// AudioChannels defaults to 1.
	AudioChannels int
	// RejectLogins makes the first N logins answer with a rejection (C=5).
	RejectLogins int
	// RejectAudioSelection answers a LIVE request that asks for audio with C=1,
	// so the bridge must fall back to video only.
	RejectAudioSelection bool
	// Logf receives protocol-level events; optional.
	Logf func(format string, args ...any)
}

// Fault stages and modes accepted by FailNext.
var faultModes = map[string][]string{
	"login": {"close-connection", "login-timeout", "flagged-header", "wrong-packet-kind",
		"invalid-json", "missing-result", "unsupported-version"},
	"live":  {"invalid-json", "missing-result", "rejected-result"},
	"media": {"unsupported-type", "truncated-video", "stall"},
}

// TalkFrame is one client-to-camera audio packet received during talkback.
type TalkFrame struct {
	At      time.Time
	PTS     uint32
	Payload []byte
}

// Snapshot is a consistent copy of the observable camera state.
type Snapshot struct {
	Logins        int
	LiveRequests  int
	Connections   int // open TCP connections
	Streaming     int // connections currently receiving media
	VideoFrames   int // video packets written, all sessions
	AudioFrames   int
	TalkFrames    int
	Directions    []int // every Q=5 direction received, in order
	ProtocolError []string
	LastFault     map[string]string // stage -> last fault applied
}

// Camera is a running simulator. It is safe for concurrent use.
type Camera struct {
	opts  Options
	key   string
	tcp   net.Listener
	udp   net.PacketConn
	start time.Time

	ctx    context.Context
	cancel context.CancelFunc
	wg     sync.WaitGroup

	mu       sync.Mutex
	changed  chan struct{}
	snapshot Snapshot
	conns    map[net.Conn]struct{}
	pending  map[string]string
	video    []Frame
	videoVer int
	talk     []TalkFrame
}

// Listen starts a camera. The caller must Close it.
func Listen(opts Options) (*Camera, error) {
	if net.ParseIP(opts.Host).To4() == nil {
		return nil, fmt.Errorf("netvuetest: invalid camera host %q", opts.Host)
	}
	if opts.DiscoveryPort == 0 {
		opts.DiscoveryPort = discoveryPort
	}
	if opts.DiscoveryBind == "" {
		opts.DiscoveryBind = opts.Host
	}
	if opts.FrameInterval == 0 {
		opts.FrameInterval = 100 * time.Millisecond
	}
	if opts.AudioRate == 0 {
		opts.AudioRate = 8000
	}
	if opts.AudioChannels == 0 {
		opts.AudioChannels = 1
	}
	if opts.Video == nil {
		opts.Video = SolidH264(64, 64, 255, 0, 0, 10)
	}
	if opts.Audio && len(opts.AudioFrames) == 0 {
		opts.AudioFrames = SilentADTS(opts.AudioRate, opts.AudioChannels, 8)
	}
	udp, err := net.ListenPacket("udp4", net.JoinHostPort(opts.DiscoveryBind, strconv.Itoa(opts.DiscoveryPort)))
	if err != nil {
		return nil, fmt.Errorf("netvuetest: discovery socket: %w", err)
	}
	tcp, err := net.Listen("tcp4", net.JoinHostPort(opts.Host, strconv.Itoa(opts.TCPPort)))
	if err != nil {
		_ = udp.Close()
		return nil, fmt.Errorf("netvuetest: camera socket: %w", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	c := &Camera{
		opts: opts, key: DefaultKey, tcp: tcp, udp: udp, start: time.Now(),
		ctx: ctx, cancel: cancel, changed: make(chan struct{}),
		conns: map[net.Conn]struct{}{}, pending: map[string]string{},
		video: opts.Video,
	}
	c.snapshot.LastFault = map[string]string{}
	c.wg.Add(2)
	go c.discoveryLoop()
	go c.acceptLoop()
	return c, nil
}

// Start runs a camera for the lifetime of the test. A missing loopback address
// or discovery port is an unavailable prerequisite, not a product failure.
func Start(t TB, opts Options) *Camera {
	t.Helper()
	c, err := Listen(opts)
	if err != nil {
		RequireIntegration(t, "start simulated camera: %v", err)
		return nil
	}
	if opts.Logf == nil {
		c.opts.Logf = t.Logf
	}
	t.Cleanup(c.Close)
	return c
}

// Host is the camera IPv4 address.
func (c *Camera) Host() string { return c.opts.Host }

// Port is the TCP port announced by discovery.
func (c *Camera) Port() int { return c.tcp.Addr().(*net.TCPAddr).Port }

// Close stops all sockets and goroutines. It is idempotent.
func (c *Camera) Close() {
	c.cancel()
	_ = c.tcp.Close()
	_ = c.udp.Close()
	c.Drop()
	c.wg.Wait()
}

func (c *Camera) logf(format string, args ...any) {
	if c.opts.Logf != nil {
		c.opts.Logf("camera "+c.opts.Host+": "+format, args...)
	}
}

// update mutates the state under the lock and wakes every waiter.
func (c *Camera) update(change func(*Snapshot)) {
	c.mu.Lock()
	change(&c.snapshot)
	close(c.changed)
	c.changed = make(chan struct{})
	c.mu.Unlock()
}

// State returns a snapshot.
func (c *Camera) State() Snapshot {
	c.mu.Lock()
	defer c.mu.Unlock()
	s := c.snapshot
	s.Directions = append([]int(nil), s.Directions...)
	s.ProtocolError = append([]string(nil), s.ProtocolError...)
	s.LastFault = map[string]string{}
	for k, v := range c.snapshot.LastFault {
		s.LastFault[k] = v
	}
	return s
}

// Wait blocks until predicate holds or ctx ends. It is event driven: it wakes
// only when the camera state changes.
func (c *Camera) Wait(ctx context.Context, what string, predicate func(Snapshot) bool) error {
	for {
		c.mu.Lock()
		changed := c.changed
		c.mu.Unlock()
		if s := c.State(); predicate(s) {
			return nil
		}
		select {
		case <-changed:
		case <-ctx.Done():
			if predicate(c.State()) {
				return nil
			}
			return fmt.Errorf("waiting for camera %s (%s): %w; state %+v", c.opts.Host, what, ctx.Err(), c.State())
		}
	}
}

// Talk returns the talkback packets received so far.
func (c *Camera) Talk() []TalkFrame {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]TalkFrame(nil), c.talk...)
}

// FailNext makes the next connection fail once at the given stage. Stages are
// login, live and media; see faultModes. Pair it with Drop to trigger a
// reconnection.
func (c *Camera) FailNext(stage, fault string) error {
	valid := false
	for _, mode := range faultModes[stage] {
		valid = valid || mode == fault
	}
	if !valid {
		return fmt.Errorf("unsupported fault %s/%s", stage, fault)
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.pending[stage] != "" {
		return fmt.Errorf("a %s fault is already pending", stage)
	}
	c.pending[stage] = fault
	return nil
}

func (c *Camera) takeFault(stage string) string {
	c.mu.Lock()
	fault := c.pending[stage]
	delete(c.pending, stage)
	if fault != "" {
		c.snapshot.LastFault[stage] = fault
		close(c.changed)
		c.changed = make(chan struct{})
	}
	c.mu.Unlock()
	return fault
}

// Drop abruptly closes every open camera connection, like a pulled cable.
func (c *Camera) Drop() {
	c.mu.Lock()
	open := make([]net.Conn, 0, len(c.conns))
	for conn := range c.conns {
		open = append(open, conn)
	}
	c.mu.Unlock()
	for _, conn := range open {
		_ = conn.Close()
	}
}

// SetVideo replaces the looping video; frames must be non-empty. The slice and
// frame data are retained without copying, and PTS is not reset.
func (c *Camera) SetVideo(frames []Frame) {
	c.mu.Lock()
	c.video = frames
	c.videoVer++
	c.mu.Unlock()
}

// Command encodes one framed command packet.
func Command(units ...map[string]any) []byte {
	body, err := json.Marshal(units)
	if err != nil {
		panic(err)
	}
	return Packet(kindCommand, 0, body)
}

// Packet encodes a framed packet with an arbitrary flag, for faulty traffic.
func Packet(kind, flag uint16, body []byte) []byte {
	packet := make([]byte, headerSize+len(body))
	binary.LittleEndian.PutUint32(packet, uint32(len(body)))
	binary.LittleEndian.PutUint16(packet[12:], flag)
	binary.LittleEndian.PutUint16(packet[14:], kind)
	copy(packet[headerSize:], body)
	return packet
}

// ReadPacket reads one framed packet and validates its header.
func ReadPacket(r io.Reader) (flag, kind uint16, body []byte, err error) {
	var header [headerSize]byte
	if _, err = io.ReadFull(r, header[:]); err != nil {
		return
	}
	size := binary.LittleEndian.Uint32(header[:4])
	flag = binary.LittleEndian.Uint16(header[12:])
	kind = binary.LittleEndian.Uint16(header[14:])
	if size == 0 || size >= maxBody {
		return 0, 0, nil, fmt.Errorf("body length %d out of range", size)
	}
	body = make([]byte, size)
	_, err = io.ReadFull(r, body)
	return
}

func (c *Camera) discoveryLoop() {
	defer c.wg.Done()
	buffer := make([]byte, 65535)
	for {
		n, remote, err := c.udp.ReadFrom(buffer)
		if err != nil {
			return
		}
		if n < headerSize || binary.LittleEndian.Uint16(buffer[14:]) != kindCommand ||
			int(binary.LittleEndian.Uint32(buffer))+headerSize != n {
			c.protocolError("malformed discovery request")
			continue
		}
		unit, ok := firstUnit(buffer[headerSize:n])
		if !ok || unit.Q != 8 || unit.V != 2 {
			c.protocolError("unexpected discovery request")
			continue
		}
		reply := Command(map[string]any{
			"A": 8, "V": 2, "C": 0,
			"P": []any{"sim-device", "sim-firmware", c.opts.Host, c.Port(), c.key, []bool{c.opts.Audio}},
		})
		if _, err := c.udp.WriteTo(reply, remote); err != nil {
			return
		}
	}
}

type unit struct {
	Q, V, A int
	C       *int
	P       []json.RawMessage
}

func firstUnit(body []byte) (unit, bool) {
	var units []unit
	if json.Unmarshal(body, &units) != nil || len(units) == 0 {
		return unit{}, false
	}
	return units[0], true
}

func (c *Camera) protocolError(reason string) {
	c.logf("protocol error: %s", reason)
	c.update(func(s *Snapshot) { s.ProtocolError = append(s.ProtocolError, reason) })
}

func (c *Camera) acceptLoop() {
	defer c.wg.Done()
	for {
		conn, err := c.tcp.Accept()
		if err != nil {
			return
		}
		c.mu.Lock()
		if c.ctx.Err() != nil {
			c.mu.Unlock()
			_ = conn.Close()
			return
		}
		c.conns[conn] = struct{}{}
		c.mu.Unlock()
		c.update(func(s *Snapshot) { s.Connections++ })
		c.wg.Add(1)
		go func() {
			defer c.wg.Done()
			c.serve(conn)
			_ = conn.Close()
			c.mu.Lock()
			delete(c.conns, conn)
			c.mu.Unlock()
			c.update(func(s *Snapshot) { s.Connections-- })
		}()
	}
}

type session struct {
	conn      net.Conn
	writeMu   sync.Mutex
	direction atomic.Int32
	audio     atomic.Bool // audio channel selected
	streaming atomic.Bool
}

func (s *session) write(packet []byte) error {
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	_, err := s.conn.Write(packet)
	return err
}

func (c *Camera) serve(conn net.Conn) {
	ctx, cancel := context.WithCancel(c.ctx)
	defer cancel()
	s := &session{conn: conn}
	defer func() {
		if s.streaming.Load() {
			c.update(func(sn *Snapshot) { sn.Streaming-- })
		}
	}()
	// The first packet must be a login.
	flag, kind, body, err := ReadPacket(conn)
	if err != nil || flag != 0 || kind != kindCommand {
		c.protocolError("invalid login packet")
		return
	}
	login, ok := firstUnit(body)
	if !ok || login.Q != 1 || len(login.P) < 5 {
		c.protocolError("expected login command")
		return
	}
	var key string
	_ = json.Unmarshal(login.P[0], &key)
	var logins int
	c.update(func(sn *Snapshot) { sn.Logins++; logins = sn.Logins })

	switch c.takeFault("login") {
	case "":
	case "close-connection":
		return
	case "login-timeout":
		// Stay silent until the bridge gives up and closes.
		_, _ = io.Copy(io.Discard, conn)
		return
	case "flagged-header":
		_ = s.write(Packet(kindCommand, 1, []byte("x")))
		return
	case "wrong-packet-kind":
		_ = s.write(Packet(kindMedia, 0, []byte("x")))
		return
	case "invalid-json":
		_ = s.write(Packet(kindCommand, 0, []byte("{")))
		return
	case "missing-result":
		_ = s.write(Command(map[string]any{"A": 1, "V": 1}))
		return
	case "unsupported-version":
		_ = s.write(Command(map[string]any{"A": 1, "V": 99, "C": 0}))
		return
	}
	result := 0
	if key != c.key || logins <= c.opts.RejectLogins {
		result = 5
	}
	if err := s.write(Command(map[string]any{"A": 1, "V": 1, "C": result})); err != nil || result != 0 {
		return
	}

	for {
		flag, kind, body, err := ReadPacket(conn)
		if err != nil {
			return
		}
		if flag != 0 {
			c.protocolError("flagged client packet")
			return
		}
		switch kind {
		case kindCommand:
			if !c.command(ctx, s, body) {
				return
			}
		case kindMedia:
			c.clientMedia(s, body)
		default:
			c.protocolError("unexpected client packet kind " + strconv.Itoa(int(kind)))
			return
		}
	}
}

// command handles one client command; false closes the connection.
func (c *Camera) command(ctx context.Context, s *session, body []byte) bool {
	u, ok := firstUnit(body)
	if !ok {
		c.protocolError("invalid client command")
		return false
	}
	switch u.Q {
	case 3:
		return c.live(ctx, s, u)
	case 5:
		var direction int
		if len(u.P) != 1 || json.Unmarshal(u.P[0], &direction) != nil || direction < 0 || direction > 3 {
			c.protocolError("invalid audio direction")
			return false
		}
		s.direction.Store(int32(direction))
		c.update(func(sn *Snapshot) { sn.Directions = append(sn.Directions, direction) })
		return s.write(Command(map[string]any{"A": 5, "V": 1, "C": 0})) == nil
	}
	return true
}

func (c *Camera) live(ctx context.Context, s *session, u unit) bool {
	c.update(func(sn *Snapshot) { sn.LiveRequests++ })
	var channels [][]int
	if len(u.P) != 3 || json.Unmarshal(u.P[1], &channels) != nil {
		c.protocolError("invalid LIVE request")
		return false
	}
	wantAudio := false
	for _, channel := range channels {
		wantAudio = wantAudio || (len(channel) > 0 && channel[0] == 2)
	}
	switch c.takeFault("live") {
	case "":
	case "invalid-json":
		_ = s.write(Packet(kindCommand, 0, []byte("{")))
		return false
	case "missing-result":
		_ = s.write(Command(map[string]any{"A": 3, "V": 2}))
		return false
	case "rejected-result":
		_ = s.write(Command(map[string]any{"A": 3, "V": 2, "C": 1}))
		return false
	}
	if wantAudio && (!c.opts.Audio || c.opts.RejectAudioSelection) {
		return s.write(Command(map[string]any{"A": 3, "V": 2, "C": 1})) == nil
	}
	selected := [][]any{{1, 1, 1, []int{0}}}
	if wantAudio {
		selected = append(selected, []any{2, 1, 2, []int{c.opts.AudioRate, 1, c.opts.AudioChannels}})
	}
	s.audio.Store(wantAudio)
	if err := s.write(Command(map[string]any{"A": 3, "V": 2, "C": 0, "P": selected})); err != nil {
		return false
	}
	if s.streaming.CompareAndSwap(false, true) {
		c.update(func(sn *Snapshot) { sn.Streaming++ })
		c.wg.Add(1)
		go func() {
			defer c.wg.Done()
			c.pump(ctx, s)
		}()
	}
	return true
}

func (c *Camera) clientMedia(s *session, body []byte) {
	if len(body) < 6 || body[0] != 0x20 || s.direction.Load() != 3 {
		c.protocolError("unexpected client media packet")
		return
	}
	frame := TalkFrame{
		At: time.Now(), PTS: binary.LittleEndian.Uint32(body[1:5]),
		Payload: append([]byte(nil), body[5:]...),
	}
	c.mu.Lock()
	c.talk = append(c.talk, frame)
	c.mu.Unlock()
	c.update(func(sn *Snapshot) { sn.TalkFrames++ })
}

func mediaPacket(header byte, pts uint32, payload []byte) []byte {
	body := make([]byte, 5+len(payload))
	body[0] = header
	binary.LittleEndian.PutUint32(body[1:], pts)
	copy(body[5:], payload)
	return Packet(kindMedia, 0, body)
}

// pump sends video and audio at their natural cadence until the connection ends.
// PTS values are milliseconds since the camera started and keep increasing
// across sessions, as a device clock would.
func (c *Camera) pump(ctx context.Context, s *session) {
	baseMS := uint32(time.Since(c.start) / time.Millisecond)
	videoTick := time.NewTicker(c.opts.FrameInterval)
	defer videoTick.Stop()
	var audioTick <-chan time.Time
	audioStep := time.Duration(0)
	if s.audio.Load() {
		audioStep = time.Duration(int64(time.Second) * 1024 / int64(c.opts.AudioRate))
		ticker := time.NewTicker(audioStep)
		defer ticker.Stop()
		audioTick = ticker.C
	}
	var videoIndex, audioIndex int
	videoPTS := baseMS
	videoStep := uint32(c.opts.FrameInterval / time.Millisecond)
	lastVersion := -1
	sendVideo := func() bool {
		c.mu.Lock()
		frames, version := c.video, c.videoVer
		c.mu.Unlock()
		if version != lastVersion {
			lastVersion, videoIndex = version, 0
		}
		frame := frames[videoIndex%len(frames)]
		switch c.takeFault("media") {
		case "unsupported-type":
			_ = s.write(Packet(kindMedia, 0, []byte{0x30, 0, 0, 0, 0, 1}))
			return false
		case "truncated-video":
			_ = s.write(Packet(kindMedia, 0, []byte{0x10, 0}))
			return false
		case "stall":
			<-ctx.Done()
			return false
		}
		header := byte(0x10)
		if frame.Key {
			header |= 1
		}
		if s.write(mediaPacket(header, videoPTS, frame.Data)) != nil {
			return false
		}
		videoIndex++
		videoPTS += videoStep
		c.update(func(sn *Snapshot) { sn.VideoFrames++ })
		return true
	}
	if !sendVideo() {
		return
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-videoTick.C:
			if !sendVideo() {
				return
			}
		case <-audioTick:
			if d := s.direction.Load(); d != 1 && d != 3 {
				continue // the camera only sends audio once a direction was requested
			}
			pts := baseMS + uint32(int64(audioIndex)*int64(audioStep)/int64(time.Millisecond))
			frame := c.opts.AudioFrames[audioIndex%len(c.opts.AudioFrames)]
			if s.write(mediaPacket(0x20, pts, frame)) != nil {
				return
			}
			audioIndex++
			c.update(func(sn *Snapshot) { sn.AudioFrames++ })
		}
	}
}
