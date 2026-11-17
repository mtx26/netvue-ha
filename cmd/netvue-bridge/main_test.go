package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/mtx26/netvue-copilot-phase1/internal/netvue"
)

func TestRunCommandSanitizesSessionErrors(t *testing.T) {
	var logs bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	defer slog.SetDefault(previous)

	runTest := func(context.Context, string, string, string, time.Duration) error {
		return fmt.Errorf("%w: secret payload", netvue.ErrProtocol)
	}
	code := runCommand(context.Background(), []string{
		"test", "--source", "192.0.2.10", "--camera", "192.0.2.20",
		"--rtsp", "rtsp://127.0.0.1:8554/test", "--duration", "1s",
	}, runTest, nil, nil)
	if code != 1 || !strings.Contains(logs.String(), "Invalid Netvue protocol data") ||
		strings.Contains(logs.String(), "secret payload") {
		t.Fatalf("exit = %d, log = %q", code, logs.String())
	}
}

func TestRunCommandUsesBoundedSessionForTest(t *testing.T) {
	called := false
	runTest := func(_ context.Context, source, camera, rtspURL string, duration time.Duration) error {
		called = true
		if source != "192.0.2.10" || camera != "192.0.2.20" ||
			rtspURL != "rtsp://127.0.0.1:8554/test" || duration != 15*time.Second {
			t.Fatalf("test session args = %q %q %q %s", source, camera, rtspURL, duration)
		}
		return nil
	}
	runContinuous := func(context.Context, string, string, string) error {
		t.Fatal("continuous runner called for test command")
		return nil
	}
	runManager := func(context.Context, string, string) error {
		t.Fatal("manager runner called for test command")
		return nil
	}

	code := runCommand(context.Background(), []string{
		"test",
		"--source", "192.0.2.10",
		"--camera", "192.0.2.20",
		"--rtsp", "rtsp://127.0.0.1:8554/test",
		"--duration", "15s",
	}, runTest, runContinuous, runManager)
	if code != 0 || !called {
		t.Fatalf("runCommand() = %d, test runner called = %v", code, called)
	}
}

func TestRunCommandUsesContinuousSupervisorForRun(t *testing.T) {
	called := false
	runTest := func(context.Context, string, string, string, time.Duration) error {
		t.Fatal("bounded runner called for run command")
		return nil
	}
	runContinuous := func(_ context.Context, source, camera, rtspURL string) error {
		called = true
		if source != "192.0.2.10" || camera != "192.0.2.20" ||
			rtspURL != "rtsp://127.0.0.1:8554/test" {
			t.Fatalf("continuous session args = %q %q %q", source, camera, rtspURL)
		}
		return nil
	}
	runManager := func(context.Context, string, string) error {
		t.Fatal("manager runner called for run command")
		return nil
	}

	code := runCommand(context.Background(), []string{
		"run",
		"--source", "192.0.2.10",
		"--camera", "192.0.2.20",
		"--rtsp", "rtsp://127.0.0.1:8554/test",
	}, runTest, runContinuous, runManager)
	if code != 0 || !called {
		t.Fatalf("runCommand() = %d, continuous runner called = %v", code, called)
	}
}

func TestParseTestDurationRequiresPositiveValueWithoutUpperLimit(t *testing.T) {
	args := []string{
		"--source", "192.0.2.10",
		"--camera", "192.0.2.20",
		"--rtsp", "rtsp://127.0.0.1:8554/test",
	}
	for _, test := range []struct {
		name        string
		duration    string
		want        time.Duration
		wantInvalid bool
	}{
		{name: "missing", wantInvalid: true},
		{name: "zero", duration: "0s", wantInvalid: true},
		{name: "negative", duration: "-1s", wantInvalid: true},
		{name: "malformed", duration: "later", wantInvalid: true},
		{name: "over 24 hours", duration: "25h", want: 25 * time.Hour},
	} {
		t.Run(test.name, func(t *testing.T) {
			testArgs := append([]string(nil), args...)
			if test.duration != "" {
				testArgs = append(testArgs, "--duration", test.duration)
			}
			config, err := parseCommandFlags("test", testArgs)
			if test.wantInvalid {
				if err == nil {
					t.Fatal("parseCommandFlags() accepted an invalid duration")
				}
				return
			}
			if err != nil {
				t.Fatalf("parseCommandFlags() error = %v", err)
			}
			if config.duration != test.want {
				t.Fatalf("parsed duration = %s, want %s", config.duration, test.want)
			}
		})
	}
}

func TestRunCommandRejectsInvalidConfigurationBeforeStarting(t *testing.T) {
	called := false
	runTest := func(context.Context, string, string, string, time.Duration) error {
		called = true
		return nil
	}
	runContinuous := func(context.Context, string, string, string) error {
		called = true
		return nil
	}
	runManager := func(context.Context, string, string) error {
		called = true
		return nil
	}

	code := runCommand(context.Background(), []string{"run", "--source", "192.0.2.10"},
		runTest, runContinuous, runManager)
	if code != 2 || called {
		t.Fatalf("runCommand() = %d, runner called = %v", code, called)
	}
}

func TestRunCommandTreatsCanceledContextAsNormalStop(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	called := false
	runTest := func(context.Context, string, string, string, time.Duration) error {
		called = true
		return errors.New("must not run")
	}
	runContinuous := func(context.Context, string, string, string) error {
		called = true
		return errors.New("must not run")
	}
	runManager := func(context.Context, string, string) error {
		called = true
		return errors.New("must not run")
	}

	code := runCommand(ctx, []string{
		"run",
		"--source", "192.0.2.10",
		"--camera", "192.0.2.20",
		"--rtsp", "rtsp://127.0.0.1:8554/test",
	}, runTest, runContinuous, runManager)
	if code != 0 || called {
		t.Fatalf("runCommand() = %d, runner called = %v", code, called)
	}
}

func TestRunCommandReportsShutdownErrorAfterContextCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	called := false
	runManager := func(context.Context, string, string) error {
		called = true
		cancel()
		return errors.New("shutdown deadline exceeded")
	}
	runTest := func(context.Context, string, string, string, time.Duration) error {
		t.Fatal("test runner called for serve command")
		return nil
	}
	runContinuous := func(context.Context, string, string, string) error {
		t.Fatal("single-camera runner called for serve command")
		return nil
	}

	code := runCommand(ctx, []string{"serve", "--source", "192.168.10.10"},
		runTest, runContinuous, runManager)
	if code != 1 || !called {
		t.Fatalf("runCommand() = %d, manager runner called = %v", code, called)
	}
}

func TestRunCommandUsesManagerForServe(t *testing.T) {
	called := false
	runTest := func(context.Context, string, string, string, time.Duration) error {
		t.Fatal("bounded runner called for serve command")
		return nil
	}
	runContinuous := func(context.Context, string, string, string) error {
		t.Fatal("single-camera runner called for serve command")
		return nil
	}
	runManager := func(_ context.Context, source, listenAddress string) error {
		called = true
		if source != "192.168.10.10" || listenAddress != "127.0.0.1:8765" {
			t.Fatalf("manager args = %q %q", source, listenAddress)
		}
		return nil
	}

	code := runCommand(context.Background(), []string{"serve", "--source", "192.168.10.10"},
		runTest, runContinuous, runManager)
	if code != 0 || !called {
		t.Fatalf("runCommand() = %d, manager runner called = %v", code, called)
	}
}

func TestParseServeRequiresSourceOnly(t *testing.T) {
	config, err := parseCommandFlags("serve", []string{"--source", "192.168.10.10"})
	if err != nil {
		t.Fatalf("parseCommandFlags() error = %v", err)
	}
	if config.listenAddress != "127.0.0.1:8765" {
		t.Fatalf("serve listen address = %q", config.listenAddress)
	}
	if _, err := parseCommandFlags("serve", []string{"--source", "192.168.10.10", "--camera", "192.168.10.20"}); err == nil {
		t.Fatal("serve accepted a single-camera flag")
	}
	if _, err := parseCommandFlags("serve", []string{"--source", "192.168.10.10", "--rtsp-address", "127.0.0.1:8554"}); err == nil {
		t.Fatal("serve accepted an RTSP address that is fixed by the Home Assistant contract")
	}
}
