package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/mtx26/netvue-copilot-phase1/netvue-go/internal/netvue"
)

func main() {
	os.Exit(run())
}

func run() int {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return runCommand(ctx, os.Args[1:], netvue.RunSession, netvue.RunContinuous)
}

type commandConfig struct {
	source   string
	camera   string
	rtspURL  string
	duration time.Duration
}

func runCommand(
	ctx context.Context,
	args []string,
	runTest func(context.Context, string, string, string, time.Duration) error,
	runContinuous func(context.Context, string, string, string) error,
) int {
	if len(args) == 0 {
		printUsage(os.Stderr)
		return 2
	}

	command := args[0]
	if command != "test" && command != "run" {
		printUsage(os.Stderr)
		return 2
	}
	config, err := parseCommandFlags(command, args[1:])
	if errors.Is(err, flag.ErrHelp) {
		return 0
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 2
	}
	if ctx.Err() != nil {
		return 0
	}

	if command == "test" {
		err = runTest(ctx, config.source, config.camera, config.rtspURL, config.duration)
	} else {
		err = runContinuous(ctx, config.source, config.camera, config.rtspURL)
	}
	if err != nil {
		if ctx.Err() != nil {
			return 0
		}
		slog.Error("Netvue service stopped", "error", err)
		return 1
	}
	slog.Info("Netvue service stopped", "camera", config.camera)
	return 0
}

func parseCommandFlags(command string, args []string) (commandConfig, error) {
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(os.Stderr)
	flags.Usage = func() {
		fmt.Fprintf(flags.Output(), "usage: netvue-bridge %s --source IPv4 --camera IPv4 --rtsp URL", command)
		if command == "test" {
			fmt.Fprint(flags.Output(), " --duration 15s")
		}
		fmt.Fprintln(flags.Output())
		flags.PrintDefaults()
	}

	var config commandConfig
	flags.StringVar(&config.source, "source", "", "local LAN IPv4 address")
	flags.StringVar(&config.camera, "camera", "", "Netvue camera IPv4 address")
	flags.StringVar(&config.rtspURL, "rtsp", "", "loopback RTSP publish URL")
	if command == "test" {
		flags.DurationVar(&config.duration, "duration", 0, "positive test session duration")
	}
	if err := flags.Parse(args); err != nil {
		return commandConfig{}, err
	}
	if flags.NArg() != 0 || config.source == "" || config.camera == "" || config.rtspURL == "" {
		return commandConfig{}, errors.New("source, camera and RTSP URL flags are required")
	}
	if command == "test" && config.duration <= 0 {
		return commandConfig{}, errors.New("test duration must be positive")
	}
	return config, nil
}

func printUsage(output *os.File) {
	fmt.Fprintln(output, "usage: netvue-bridge <test|run> --source IPv4 --camera IPv4 --rtsp URL")
	fmt.Fprintln(output, "use `netvue-bridge test --help` or `netvue-bridge run --help` for options")
}
