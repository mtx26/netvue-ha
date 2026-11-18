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
	for _, invalid := range [][]string{
		{"--source", "192.0.2.1", "--camera", "192.0.2.2", "--rtsp", "rtsp://127.0.0.1:8554/talk", "--duration", "1s"},
		{"--source", "192.0.2.1", "--camera", "192.0.2.2", "--rtsp", "rtsp://127.0.0.1:8554/talk", "--duration", "3m", "--audio", "clip"},
		{"--source", "192.0.2.1", "--camera", "192.0.2.2", "--rtsp", "rtsp://127.0.0.1:8554/talk", "--duration", "10s", "--audio", "clip", "--delay", "11s"},
	} {
		if _, err := parseCommandFlags("talk", invalid); err == nil {
			t.Fatalf("accepted invalid flags %v", invalid)
		}
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
