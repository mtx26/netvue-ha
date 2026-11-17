package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"

	"github.com/mtx26/netvue-copilot-phase1/netvue-go/internal/netvue"
)

func main() {
	if len(os.Args) < 2 || os.Args[1] != "session" {
		fmt.Fprintln(os.Stderr, "usage: netvue-bridge session --source IPv4 --camera IPv4 --rtsp URL --duration 15s")
		os.Exit(2)
	}
	flags := flag.NewFlagSet("session", flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	source := flags.String("source", "", "local LAN IPv4 address")
	camera := flags.String("camera", "", "Netvue camera IPv4 address")
	rtspURL := flags.String("rtsp", "", "loopback RTSP publish URL")
	duration := flags.Duration("duration", 0, "maximum session duration")
	if err := flags.Parse(os.Args[2:]); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return
		}
		os.Exit(2)
	}
	if flags.NArg() != 0 || *source == "" || *camera == "" || *rtspURL == "" || *duration <= 0 {
		fmt.Fprintln(os.Stderr, "all session flags are required; see --help")
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()

	if err := netvue.RunSession(ctx, *source, *camera, *rtspURL, *duration); err != nil {
		slog.Error("Netvue session stopped", "error", err)
		os.Exit(1)
	}
	slog.Info("Netvue session completed", "camera", *camera, "duration", duration.String())
}
