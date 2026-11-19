// Package e2e runs the real netvue-bridge binary against simulated Netvue
// cameras, a real MediaMTX and independent readers (ffmpeg, a pion WebRTC
// client). It needs ffmpeg and mediamtx; see netvuetest.RequireIntegration
// for the NETVUE_REQUIRE_INTEGRATION=1 behaviour.
package e2e

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"
	"testing"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue/netvuetest"
)

// Loopback group of this package's simulated cameras.
const group = 22

// The manager publishes to a fixed RTSP address (defaultRTSPAddress).
const rtspAddress = "127.0.0.1:8554"

var (
	buildOnce sync.Once
	buildPath string
	buildErr  error
)

func bridgeBinary(t *testing.T) string {
	t.Helper()
	buildOnce.Do(func() {
		dir, err := os.MkdirTemp("", "netvue-e2e-")
		if err != nil {
			buildErr = err
			return
		}
		name := "netvue-bridge"
		if runtime.GOOS == "windows" {
			name += ".exe"
		}
		buildPath = filepath.Join(dir, name)
		command := exec.Command("go", "build", "-o", buildPath, "github.com/mtx26/netvue-ha/cmd/netvue-bridge")
		if output, err := command.CombinedOutput(); err != nil {
			buildErr = fmt.Errorf("go build: %v: %s", err, output)
		}
	})
	if buildErr != nil {
		t.Fatalf("build netvue-bridge: %v", buildErr)
	}
	return buildPath
}

// Stack is MediaMTX plus one netvue-bridge process, with the control API.
type Stack struct {
	API    string
	FFmpeg string
}

type output struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (o *output) Write(p []byte) (int, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.b.Write(p)
}

func (o *output) String() string {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.b.String()
}

func startProcess(t *testing.T, name string, env []string, exe string, args ...string) *output {
	t.Helper()
	logs := &output{}
	command := exec.Command(exe, args...)
	command.Stdout, command.Stderr = logs, logs
	command.Env = append(os.Environ(), env...)
	if err := command.Start(); err != nil {
		t.Fatalf("start %s: %v", name, err)
	}
	t.Cleanup(func() {
		_ = command.Process.Kill()
		_ = command.Wait()
		if t.Failed() {
			t.Logf("%s output:\n%s", name, logs.String())
		}
	})
	return logs
}

func freePort(t *testing.T) int {
	t.Helper()
	l, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port
}

func waitTCP(t *testing.T, address string) {
	t.Helper()
	ctx, cancel := netvuetest.Within(15 * time.Second)
	defer cancel()
	var dialer net.Dialer
	for {
		conn, err := dialer.DialContext(ctx, "tcp4", address)
		if err == nil {
			_ = conn.Close()
			return
		}
		select {
		case <-ctx.Done():
			t.Fatalf("%s did not start listening: %v", address, err)
		case <-time.After(50 * time.Millisecond): // readiness poll of an external process
		}
	}
}

// StartStack launches MediaMTX on the fixed publish address and the bridge's
// control API on an ephemeral port. Missing tools or a busy 8554 follow
// NETVUE_REQUIRE_INTEGRATION.
func StartStack(t *testing.T) *Stack {
	t.Helper()
	mediamtx := netvuetest.Executable(t, "NETVUE_TEST_MEDIAMTX", "mediamtx")
	ffmpeg := netvuetest.Executable(t, "NETVUE_TEST_FFMPEG", "ffmpeg")
	if l, err := net.Listen("tcp4", rtspAddress); err != nil {
		netvuetest.RequireIntegration(t, "%s is busy: %v", rtspAddress, err)
	} else {
		_ = l.Close()
	}
	bridge := bridgeBinary(t)

	config := filepath.Join(t.TempDir(), "mediamtx.yml")
	yml := `logLevel: error
rtsp: true
rtspAddress: ` + rtspAddress + `
rtspTransports: [tcp]
rtspEncryption: "no"
rtmp: false
hls: false
webrtc: false
srt: false
moq: false
api: false
metrics: false
pprof: false
playback: false
pathDefaults:
  record: false
  overridePublisher: false
paths:
  all_others:
`
	if err := os.WriteFile(config, []byte(yml), 0o600); err != nil {
		t.Fatal(err)
	}
	startProcess(t, "mediamtx", nil, mediamtx, config)
	waitTCP(t, rtspAddress)

	api := fmt.Sprintf("127.0.0.1:%d", freePort(t))
	path := os.Getenv("PATH") + string(os.PathListSeparator) + filepath.Dir(ffmpeg)
	startProcess(t, "netvue-bridge", []string{"PATH=" + path}, bridge, "serve", "--source", "127.0.0.1", "--listen", api)
	waitTCP(t, api)
	return &Stack{API: "http://" + api, FFmpeg: ffmpeg}
}

func (s *Stack) Do(t *testing.T, method, path string, body any) (int, []byte) {
	t.Helper()
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		reader = bytes.NewReader(data)
	}
	ctx, cancel := netvuetest.Within(15 * time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, method, s.API+path, reader)
	if err != nil {
		t.Fatal(err)
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatalf("%s %s: %v", method, path, err)
	}
	defer response.Body.Close()
	data, _ := io.ReadAll(response.Body)
	return response.StatusCode, data
}

// AddCamera registers a simulated camera and returns its RTSP URL.
func (s *Stack) AddCamera(t *testing.T, host, name string) string {
	t.Helper()
	status, body := s.Do(t, http.MethodPost, "/api/v1/cameras", map[string]string{"host": host, "stream_name": name})
	if status != http.StatusAccepted && status != http.StatusOK {
		t.Fatalf("add camera %s: %d %s", host, status, body)
	}
	return "rtsp://" + rtspAddress + "/" + name
}

func ctxFor(t *testing.T, limit time.Duration) context.Context {
	t.Helper()
	ctx, cancel := netvuetest.Within(limit)
	t.Cleanup(cancel)
	return ctx
}
