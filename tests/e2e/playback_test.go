package e2e

import (
	"net/http"
	"testing"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue/netvuetest"
)

// HA control plane -> Go bridge -> simulated camera -> MediaMTX -> reader.
func TestE2EBridgeMediaMTXPlayback(t *testing.T) {
	stack := StartStack(t)
	tone, err := netvuetest.ToneADTS(stack.FFmpeg, 8000, 440, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	camera := netvuetest.Start(t, netvuetest.Options{
		Host: netvuetest.AllocHost(group), Audio: true, AudioFrames: tone,
		Video: netvuetest.SolidH264(160, 96, 255, 0, 0, 10),
	})
	url := stack.AddCamera(t, camera.Host(), "playback")
	ctx := ctxFor(t, 90*time.Second)
	if err := camera.Wait(ctx, "authenticated LIVE stream", func(s netvuetest.Snapshot) bool {
		return s.Logins >= 1 && s.LiveRequests >= 1 && s.Streaming == 1 && s.VideoFrames >= 5 && s.AudioFrames >= 3
	}); err != nil {
		t.Fatal(err)
	}
	if s := camera.State(); len(s.ProtocolError) != 0 {
		t.Fatalf("bridge violated the protocol: %v", s.ProtocolError)
	}

	decode := func(label string) {
		t.Helper()
		pixel, err := netvuetest.DecodeVideoPixel(ctx, stack.FFmpeg, url, 160, 96)
		if err != nil || !netvuetest.IsRed(pixel) {
			t.Fatalf("%s: red video not decodable: %v %v", label, pixel, err)
		}
		if peak, err := netvuetest.DecodeAudioPeak(ctx, stack.FFmpeg, url); err != nil || peak < 1000 {
			t.Fatalf("%s: audio not decodable/audible: peak %d: %v", label, peak, err)
		}
	}
	decode("initial")

	offset, err := AVOffset(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	if offset < -500*time.Millisecond || offset > 500*time.Millisecond {
		t.Fatalf("audio/video RTCP offset %v exceeds 500ms", offset)
	}

	// Reconnection: the bridge reopens the camera and publishing resumes.
	before := camera.State()
	camera.Drop()
	if err := camera.Wait(ctx, "reconnection", func(s netvuetest.Snapshot) bool {
		return s.LiveRequests > before.LiveRequests && s.Streaming == 1 && s.VideoFrames > before.VideoFrames+5
	}); err != nil {
		t.Fatal(err)
	}
	decode("after reconnection")

	// Resolution change on the camera: the bridge republishes the new size.
	camera.SetVideo(netvuetest.SolidH264(320, 192, 0, 0, 255, 10))
	deadline := ctxFor(t, 45*time.Second)
	pixel, err := netvuetest.DecodeVideoPixel(deadline, stack.FFmpeg, url, 320, 192)
	if err != nil || !netvuetest.IsBlue(pixel) {
		t.Fatalf("blue 320x192 not decodable after resolution change: %v %v", pixel, err)
	}

	// Cleanup: deleting the camera closes the TCP session and stops publishing.
	if status, body := stack.Do(t, http.MethodDelete, "/api/v1/cameras/"+camera.Host(), nil); status != http.StatusOK {
		t.Fatalf("delete: %d %s", status, body)
	}
	if err := camera.Wait(ctx, "session closed after delete", func(s netvuetest.Snapshot) bool {
		return s.Connections == 0 && s.Streaming == 0
	}); err != nil {
		t.Fatal(err)
	}
	if status, _ := stack.Do(t, http.MethodGet, "/api/v1/cameras/"+camera.Host(), nil); status != http.StatusNotFound {
		t.Fatalf("deleted camera still known: %d", status)
	}
	stopped := camera.State()
	short := ctxFor(t, 10*time.Second)
	if _, err := netvuetest.DecodeVideoPixel(short, stack.FFmpeg, url, 320, 192); err == nil {
		t.Fatal("RTSP path still serves video after the camera was deleted")
	}
	if after := camera.State(); after.LiveRequests != stopped.LiveRequests {
		t.Fatalf("bridge reconnected after delete: %d -> %d LIVE requests", stopped.LiveRequests, after.LiveRequests)
	}
}

// One camera fails; the other keeps streaming untouched, then the first one
// recovers when it comes back.
func TestE2EMultiCameraIsolation(t *testing.T) {
	stack := StartStack(t)
	hostA, hostB := netvuetest.AllocHost(group), netvuetest.AllocHost(group)
	a := netvuetest.Start(t, netvuetest.Options{Host: hostA, Video: netvuetest.SolidH264(160, 96, 255, 0, 0, 10)})
	b, err := netvuetest.Listen(netvuetest.Options{Host: hostB, Video: netvuetest.SolidH264(160, 96, 0, 0, 255, 10)})
	if err != nil {
		netvuetest.RequireIntegration(t, "cannot start camera B: %v", err)
		return
	}
	defer func() { b.Close() }()
	urlA, urlB := stack.AddCamera(t, hostA, "cam-a"), stack.AddCamera(t, hostB, "cam-b")

	ctx := ctxFor(t, 120*time.Second)
	streaming := func(s netvuetest.Snapshot) bool { return s.Streaming == 1 && s.VideoFrames >= 5 }
	for _, c := range []*netvuetest.Camera{a, b} {
		if err := c.Wait(ctx, "initial stream", streaming); err != nil {
			t.Fatal(err)
		}
	}
	if p, err := netvuetest.DecodeVideoPixel(ctx, stack.FFmpeg, urlA, 160, 96); err != nil || !netvuetest.IsRed(p) {
		t.Fatalf("camera A: %v %v", p, err)
	}
	if p, err := netvuetest.DecodeVideoPixel(ctx, stack.FFmpeg, urlB, 160, 96); err != nil || !netvuetest.IsBlue(p) {
		t.Fatalf("camera B: %v %v", p, err)
	}

	// Camera B disappears (all its sockets closed).
	beforeA := a.State()
	b.Close()
	if err := a.Wait(ctx, "A streams on during B outage", func(s netvuetest.Snapshot) bool {
		return s.VideoFrames >= beforeA.VideoFrames+10
	}); err != nil {
		t.Fatal(err)
	}
	if s := a.State(); s.LiveRequests != beforeA.LiveRequests || s.Logins != beforeA.Logins || s.Streaming != 1 {
		t.Fatalf("camera A was disturbed by camera B's failure: before %+v after %+v", beforeA, s)
	}
	if p, err := netvuetest.DecodeVideoPixel(ctx, stack.FFmpeg, urlA, 160, 96); err != nil || !netvuetest.IsRed(p) {
		t.Fatalf("camera A stopped decoding during B outage: %v %v", p, err)
	}

	// Camera B returns at the same address: its supervisor recovers alone.
	b, err = netvuetest.Listen(netvuetest.Options{Host: hostB, Video: netvuetest.SolidH264(160, 96, 0, 0, 255, 10)})
	if err != nil {
		t.Fatalf("restart camera B: %v", err)
	}
	if err := b.Wait(ctx, "B recovery", streaming); err != nil {
		t.Fatal(err)
	}
	if p, err := netvuetest.DecodeVideoPixel(ctx, stack.FFmpeg, urlB, 160, 96); err != nil || !netvuetest.IsBlue(p) {
		t.Fatalf("camera B did not recover: %v %v", p, err)
	}
	if s := a.State(); s.LiveRequests != beforeA.LiveRequests {
		t.Fatalf("camera A reconnected because of camera B: %+v", s)
	}
}
