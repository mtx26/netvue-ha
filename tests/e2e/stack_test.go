// Package e2e runs the real netvue-bridge binary against simulated Netvue
// cameras, a real MediaMTX and independent readers (ffmpeg, a pion WebRTC
// client). It needs ffmpeg and mediamtx; see netvuetest.RequireIntegration
// for the NETVUE_REQUIRE_INTEGRATION=1 behaviour.
package e2e

import (
	"bytes"
	"context"
	"debug/buildinfo"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"strings"
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

func raceInstrumented(info *debug.BuildInfo) bool {
	for _, setting := range info.Settings {
		if setting.Key == "-race" && setting.Value == "true" {
			return true
		}
	}
	return false
}

func bridgeBinary(t *testing.T) string {
	t.Helper()
	race := false
	if info, ok := debug.ReadBuildInfo(); ok {
		race = raceInstrumented(info)
	}
	if path := os.Getenv("NETVUE_TEST_GO_BRIDGE"); path != "" {
		binary, err := exec.LookPath(path)
		if err != nil {
			t.Fatalf("NETVUE_TEST_GO_BRIDGE: %v", err)
		}
		if race {
			info, err := buildinfo.ReadFile(binary)
			if err != nil {
				t.Fatalf("read NETVUE_TEST_GO_BRIDGE build info: %v", err)
			}
			if !raceInstrumented(info) {
				t.Fatal("NETVUE_TEST_GO_BRIDGE must be built with -race when running race tests")
			}
		}
		return binary
	}
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
		args := []string{"build"}
		if race {
			args = append(args, "-race")
		}
		args = append(args, "-o", buildPath, "github.com/mtx26/netvue-ha/cmd/netvue-bridge")
		command := exec.Command("go", args...)
		if output, err := command.CombinedOutput(); err != nil {
			buildErr = fmt.Errorf("go build: %v: %s", err, output)
		}
	})
	if buildErr != nil {
		t.Fatalf("build netvue-bridge: %v", buildErr)
	}
	return buildPath
}

func TestBridgeBinaryUsesPrebuiltWithoutGo(t *testing.T) {
	binary, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("NETVUE_TEST_GO_BRIDGE", binary)
	t.Setenv("PATH", t.TempDir())
	if got := bridgeBinary(t); got != binary {
		t.Fatalf("bridge binary = %q, want prebuilt %q", got, binary)
	}
}

// Stack is MediaMTX plus one netvue-bridge process, with the control API.
type Stack struct {
	API    string
	FFmpeg string
}

type output struct {
	mu      sync.Mutex
	b       bytes.Buffer
	done    chan struct{}
	waitErr error
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

func childEnvironment(parent, extra []string) []string {
	env := make([]string, 0, len(parent)+len(extra))
	for _, entry := range parent {
		key, _, ok := strings.Cut(entry, "=")
		if ok && (strings.EqualFold(key, "NETVUE_CLOUDFLARE_TURN_KEY_ID") ||
			strings.EqualFold(key, "NETVUE_CLOUDFLARE_TURN_API_TOKEN")) {
			continue
		}
		env = append(env, entry)
	}
	return append(env, extra...)
}

func TestChildEnvironmentExcludesCloudflareTURNCredentials(t *testing.T) {
	t.Setenv("NETVUE_CLOUDFLARE_TURN_KEY_ID", "dummy-key-id")
	t.Setenv("NETVUE_CLOUDFLARE_TURN_API_TOKEN", "dummy-api-token")
	t.Setenv("NETVUE_E2E_ENV_PRESERVED", "parent-value")

	env := childEnvironment(os.Environ(), []string{"NETVUE_E2E_ENV_EXTRA=extra-value"})
	foundPreserved, foundExtra := false, false
	for _, entry := range env {
		key, value, ok := strings.Cut(entry, "=")
		if !ok {
			continue
		}
		switch {
		case strings.EqualFold(key, "NETVUE_CLOUDFLARE_TURN_KEY_ID"),
			strings.EqualFold(key, "NETVUE_CLOUDFLARE_TURN_API_TOKEN"):
			t.Errorf("child environment unexpectedly contains %s", key)
		case strings.EqualFold(key, "NETVUE_E2E_ENV_PRESERVED") && value == "parent-value":
			foundPreserved = true
		case strings.EqualFold(key, "NETVUE_E2E_ENV_EXTRA") && value == "extra-value":
			foundExtra = true
		}
	}
	if !foundPreserved {
		t.Error("child environment did not preserve the parent environment")
	}
	if !foundExtra {
		t.Error("child environment did not include additional settings")
	}
}

func TestChildRaceReportsFailTheHarness(t *testing.T) {
	const testName = "TestChildRaceReportsFailTheHarness"
	if os.Getenv("NETVUE_E2E_RACE_TEST_CHILD") == "1" {
		fmt.Fprintln(os.Stderr, os.Getenv("NETVUE_E2E_RACE_TEST_OUTPUT"))
		conn, err := net.Dial("tcp", os.Getenv("NETVUE_E2E_RACE_TEST_ADDRESS"))
		if err != nil {
			t.Fatal(err)
		}
		if os.Getenv("NETVUE_E2E_RACE_TEST_MODE") == "crash" {
			os.Exit(7)
		}
		_, _ = io.Copy(io.Discard, conn)
		_ = conn.Close()
		return
	}
	if os.Getenv("NETVUE_E2E_RACE_TEST_HARNESS") == "1" {
		listener, err := net.ListenTCP("tcp4", &net.TCPAddr{IP: net.IPv4(127, 0, 0, 1)})
		if err != nil {
			t.Fatal(err)
		}
		defer listener.Close()
		if err := listener.SetDeadline(time.Now().Add(10 * time.Second)); err != nil {
			t.Fatal(err)
		}
		var conn net.Conn
		t.Cleanup(func() {
			if conn != nil {
				_ = conn.Close()
			}
		})
		logs := startProcess(t, "child", []string{
			"NETVUE_E2E_RACE_TEST_CHILD=1",
			"NETVUE_E2E_RACE_TEST_ADDRESS=" + listener.Addr().String(),
		}, os.Args[0], "-test.run=^"+testName+"$")
		conn, err = listener.Accept()
		if err != nil {
			t.Fatal(err)
		}
		if os.Getenv("NETVUE_E2E_RACE_TEST_MODE") == "crash" {
			_, _ = io.Copy(io.Discard, conn)
			select {
			case <-logs.done:
			case <-time.After(10 * time.Second):
				t.Fatal("child did not exit")
			}
		}
		return
	}

	for _, tc := range []struct {
		name   string
		output string
		mode   string
		fail   bool
	}{
		{name: "clean", output: "normal child log"},
		{name: "race", output: "WARNING: DATA RACE", fail: true},
		{name: "crash", output: "child crashed", mode: "crash", fail: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			command := exec.CommandContext(ctx, os.Args[0], "-test.run=^"+testName+"$")
			command.Env = append(os.Environ(),
				"NETVUE_E2E_RACE_TEST_HARNESS=1",
				"NETVUE_E2E_RACE_TEST_OUTPUT="+tc.output,
				"NETVUE_E2E_RACE_TEST_MODE="+tc.mode,
			)
			output, err := command.CombinedOutput()
			if ctx.Err() != nil {
				t.Fatalf("harness timed out: %v\n%s", ctx.Err(), output)
			}
			if tc.fail {
				if err == nil || !bytes.Contains(output, []byte(tc.output)) {
					t.Fatalf("child failure did not fail the harness: err=%v output=%s", err, output)
				}
			} else if err != nil {
				t.Fatalf("clean child failed the harness: %v\n%s", err, output)
			}
		})
	}
}

func startProcess(t *testing.T, name string, env []string, exe string, args ...string) *output {
	t.Helper()
	logs := &output{done: make(chan struct{})}
	command := exec.Command(exe, args...)
	command.Stdout, command.Stderr = logs, logs
	command.Env = childEnvironment(os.Environ(), env)
	if err := command.Start(); err != nil {
		t.Fatalf("start %s: %v", name, err)
	}
	go func() {
		logs.waitErr = command.Wait()
		close(logs.done)
	}()
	t.Cleanup(func() {
		exitedEarly := false
		select {
		case <-logs.done:
			exitedEarly = true
		default:
			if err := command.Process.Kill(); err != nil {
				t.Errorf("stop %s: %v", name, err)
				exitedEarly = true
			}
			select {
			case <-logs.done:
			case <-time.After(10 * time.Second):
				t.Errorf("%s did not exit after being stopped", name)
				return
			}
		}
		if exitedEarly {
			t.Errorf("%s exited unexpectedly: %v", name, logs.waitErr)
		}
		if strings.Contains(logs.String(), "WARNING: DATA RACE") {
			t.Errorf("%s reported a Go data race", name)
		}
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
	startProcess(t, "netvue-bridge", []string{
		"PATH=" + path,
		"GORACE=" + os.Getenv("GORACE") + " log_path=stderr",
	}, bridge, "serve", "--source", "127.0.0.1", "--listen", api)
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
