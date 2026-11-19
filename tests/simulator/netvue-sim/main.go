// Command netvue-sim runs one simulated Netvue camera with an HTTP control
// endpoint. It replaces the former Python synthetic camera in the
// Home Assistant -> Go -> camera end-to-end scenario.
//
//	netvue-sim -host 172.20.0.2 [-reject-initial-login] [-audio]
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue/netvuetest"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "netvue-sim:", err)
		os.Exit(1)
	}
}

func run() error {
	host := flag.String("host", "", "private IPv4 address of this camera (required)")
	port := flag.Int("port", 5555, "TCP camera port")
	control := flag.String("control", "0.0.0.0:5556", "HTTP control address")
	reject := flag.Bool("reject-initial-login", false, "reject the first login (C=5)")
	audio := flag.Bool("audio", false, "advertise and stream audio (structurally valid ADTS)")
	flag.Parse()
	ip := net.ParseIP(*host).To4()
	if ip == nil || !(ip.IsPrivate() || ip.IsLoopback()) {
		return errors.New("-host must be a private or loopback IPv4 address")
	}
	opts := netvuetest.Options{
		Host: *host, TCPPort: *port, DiscoveryBind: "0.0.0.0", Audio: *audio,
		Logf: func(format string, args ...any) { fmt.Printf(format+"\n", args...) },
	}
	if *reject {
		opts.RejectLogins = 1
	}
	camera, err := netvuetest.Listen(opts)
	if err != nil {
		return err
	}
	defer camera.Close()
	server := &http.Server{Addr: *control, Handler: camera.ControlHandler(), ReadHeaderTimeout: 3 * time.Second}
	listener, err := net.Listen("tcp", *control)
	if err != nil {
		return err
	}
	go func() { _ = server.Serve(listener) }()
	fmt.Printf("Synthetic camera ready at %s:%d\n", *host, camera.Port())

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	<-ctx.Done()
	shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	return server.Shutdown(shutdown)
}
