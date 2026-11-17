package main

import (
	"context"
	"errors"
	"testing"
	"time"
)

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

	code := runCommand(context.Background(), []string{
		"test",
		"--source", "192.0.2.10",
		"--camera", "192.0.2.20",
		"--rtsp", "rtsp://127.0.0.1:8554/test",
		"--duration", "15s",
	}, runTest, runContinuous)
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

	code := runCommand(context.Background(), []string{
		"run",
		"--source", "192.0.2.10",
		"--camera", "192.0.2.20",
		"--rtsp", "rtsp://127.0.0.1:8554/test",
	}, runTest, runContinuous)
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

	code := runCommand(context.Background(), []string{"run", "--source", "192.0.2.10"},
		runTest, runContinuous)
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

	code := runCommand(ctx, []string{
		"run",
		"--source", "192.0.2.10",
		"--camera", "192.0.2.20",
		"--rtsp", "rtsp://127.0.0.1:8554/test",
	}, runTest, runContinuous)
	if code != 0 || called {
		t.Fatalf("runCommand() = %d, runner called = %v", code, called)
	}
}
