package main

import (
	"bytes"
	"context"
	"fmt"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue"
)

func TestTalkCommand(t *testing.T) {
	called := false
	runner := func(_ context.Context, source, camera, rtsp string, duration time.Duration, file string, delay time.Duration) error {
		called = true
		if source != "192.0.2.1" || camera != "192.0.2.2" || rtsp != "rtsp://127.0.0.1:8554/talk" ||
			duration != 45*time.Second || file != "clip.aac" || delay != 3*time.Second {
			t.Fatalf("unexpected talk parameters")
		}
		return nil
	}

	args := []string{"talk", "--source", "192.0.2.1", "--camera", "192.0.2.2", "--rtsp", "rtsp://127.0.0.1:8554/talk", "--duration", "45s", "--audio", "clip.aac", "--delay", "3s"}
	if code := runCommand(context.Background(), args, nil, nil, nil, runner); code != 0 || !called {
		t.Fatalf("code=%d called=%t", code, called)
	}
}

func TestParseTalkCommandLimitsAndRequiredFlags(t *testing.T) {
	base := []string{"--source", "192.0.2.1", "--camera", "192.0.2.2",
		"--rtsp", "rtsp://127.0.0.1:8554/talk"}
	for _, test := range []struct {
		name    string
		flags   []string
		wantErr bool
	}{
		{name: "missing duration", flags: []string{"--audio", "clip.aac"}, wantErr: true},
		{name: "missing audio", flags: []string{"--duration", "45s"}, wantErr: true},
		{name: "malformed duration", flags: []string{"--duration", "later", "--audio", "clip.aac"}, wantErr: true},
		{name: "zero duration", flags: []string{"--duration", "0s", "--audio", "clip.aac"}, wantErr: true},
		{name: "negative duration", flags: []string{"--duration", "-1s", "--audio", "clip.aac"}, wantErr: true},
		{name: "duration over maximum", flags: []string{"--duration", "2m1s", "--audio", "clip.aac"}, wantErr: true},
		{name: "negative delay", flags: []string{"--duration", "45s", "--audio", "clip.aac", "--delay", "-1ns"}, wantErr: true},
		{name: "delay over maximum", flags: []string{"--duration", "45s", "--audio", "clip.aac", "--delay", "10s1ns"}, wantErr: true},
		{name: "maximum duration and delay", flags: []string{"--duration", "2m", "--audio", "clip.aac", "--delay", "10s"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			args := append(append([]string(nil), base...), test.flags...)
			_, err := parseCommandFlags("talk", args)
			if (err != nil) != test.wantErr {
				t.Fatalf("parseCommandFlags() error = %v, wantErr %v", err, test.wantErr)
			}
		})
	}
}

func TestTalkCommandRequiresRunner(t *testing.T) {
	args := []string{"talk", "--source", "192.0.2.1", "--camera", "192.0.2.2",
		"--rtsp", "rtsp://127.0.0.1:8554/talk", "--duration", "45s", "--audio", "clip.aac"}
	if code := runCommand(context.Background(), args, nil, nil, nil, nil); code != 1 {
		t.Fatalf("runCommand() = %d, want 1 when talkback runner is unavailable", code)
	}
}

func TestTalkCommandSanitizesSessionError(t *testing.T) {
	var logs bytes.Buffer
	previous := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	defer slog.SetDefault(previous)
	runner := func(context.Context, string, string, string, time.Duration, string, time.Duration) error {
		return fmt.Errorf("%w: secret session payload", netvue.ErrProtocol)
	}
	args := []string{"talk", "--source", "192.0.2.1", "--camera", "192.0.2.2",
		"--rtsp", "rtsp://127.0.0.1:8554/talk", "--duration", "45s", "--audio", "clip.aac"}
	if code := runCommand(context.Background(), args, nil, nil, nil, runner); code != 1 {
		t.Fatalf("exit = %d", code)
	}
	if !strings.Contains(logs.String(), "Invalid Netvue protocol data") || strings.Contains(logs.String(), "secret session payload") {
		t.Fatalf("unsafe diagnostic: %q", logs.String())
	}
}
