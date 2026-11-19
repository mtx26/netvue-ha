package e2e

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os/exec"
	"slices"
	"sort"
	"sync"
	"testing"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue/netvuetest"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media"
)

// opusTone returns 20 ms Opus packets of a sine wave from ffmpeg (libopus).
func opusTone(t *testing.T, ffmpeg string, hz int, seconds int) [][]byte {
	t.Helper()
	command := exec.Command(ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
		"-i", fmt.Sprintf("sine=frequency=%d:sample_rate=48000:duration=%d", hz, seconds),
		"-ac", "1", "-c:a", "libopus", "-application", "voip", "-frame_duration", "20", "-f", "ogg", "pipe:1")
	data, err := command.Output()
	if err != nil {
		netvuetest.RequireIntegration(t, "ffmpeg cannot encode Opus (libopus missing?): %v", err)
		return nil
	}
	var packets [][]byte
	var current []byte
	for len(data) >= 27 {
		if string(data[:4]) != "OggS" {
			t.Fatal("invalid Ogg page")
		}
		segments := int(data[26])
		table := data[27 : 27+segments]
		data = data[27+segments:]
		for _, size := range table {
			current = append(current, data[:size]...)
			data = data[size:]
			if size < 255 {
				packets = append(packets, current)
				current = nil
			}
		}
	}
	// The first two packets are OpusHead and OpusTags.
	if len(packets) < 50 {
		t.Fatalf("only %d Ogg packets", len(packets))
	}
	return packets[2:]
}

func signalWebRTC(t *testing.T, stack *Stack, host string, browser *webrtc.PeerConnection, candidates func() []webrtc.ICECandidateInit, connected <-chan struct{}) {
	t.Helper()
	offer, err := browser.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	gathered := webrtc.GatheringCompletePromise(browser)
	if err := browser.SetLocalDescription(offer); err != nil {
		t.Fatal(err)
	}
	<-gathered
	path := "/api/v1/cameras/" + host + "/webrtc/e2e"
	if status, body := stack.Do(t, http.MethodPut, path, map[string]string{"offer": browser.LocalDescription().SDP}); status != http.StatusCreated {
		t.Fatalf("PUT offer: %d %s", status, body)
	}
	t.Cleanup(func() { stack.Do(t, http.MethodDelete, path, nil) })
	for _, c := range candidates() {
		if status, body := stack.Do(t, http.MethodPost, path+"/candidates", map[string]any{"candidate": c}); status != http.StatusNoContent {
			t.Fatalf("candidate: %d %s", status, body)
		}
	}
	ctx := ctxFor(t, 30*time.Second)
	var after uint64
	for {
		select {
		case <-connected:
			return
		case <-ctx.Done():
			t.Fatalf("WebRTC did not connect: %s / %s", browser.ConnectionState(), browser.ICEConnectionState())
		default:
		}
		pollCtx, cancel := context.WithTimeout(ctx, time.Second)
		request, err := http.NewRequestWithContext(pollCtx, http.MethodGet, fmt.Sprintf("%s%s/events?after=%d", stack.API, path, after), nil)
		if err != nil {
			cancel()
			t.Fatal(err)
		}
		response, err := http.DefaultClient.Do(request)
		if err != nil { // long poll expired without events
			cancel()
			continue
		}
		body, _ := io.ReadAll(response.Body)
		_ = response.Body.Close()
		cancel()
		if response.StatusCode != http.StatusOK {
			t.Fatalf("events: %d %s", response.StatusCode, body)
		}
		var reply struct {
			Events []struct {
				Seq       uint64                   `json:"seq"`
				Type      string                   `json:"type"`
				Answer    string                   `json:"answer"`
				Candidate *webrtc.ICECandidateInit `json:"candidate"`
				Code      string                   `json:"code"`
			} `json:"events"`
		}
		if err := json.Unmarshal(body, &reply); err != nil {
			t.Fatal(err)
		}
		for _, e := range reply.Events {
			after = e.Seq
			switch e.Type {
			case "answer":
				if err := browser.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: e.Answer}); err != nil {
					t.Fatal(err)
				}
			case "candidate":
				if err := browser.AddICECandidate(*e.Candidate); err != nil {
					t.Fatal(err)
				}
			case "error":
				t.Fatalf("bridge WebRTC error: %s", e.Code)
			}
		}
	}
}

// Browser microphone (Opus over WebRTC) -> bridge -> AAC ADTS -> simulated
// camera. The camera's recording is decoded independently and its cadence
// is checked.
func TestE2EWebRTCTalkback(t *testing.T) {
	stack := StartStack(t)
	tone, err := netvuetest.ToneADTS(stack.FFmpeg, 8000, 440, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	camera := netvuetest.Start(t, netvuetest.Options{
		Host: netvuetest.AllocHost(group), Audio: true, AudioFrames: tone,
		Video: netvuetest.SolidH264(160, 96, 255, 0, 0, 10),
	})
	stack.AddCamera(t, camera.Host(), "talk")
	ctx := ctxFor(t, 90*time.Second)
	if err := camera.Wait(ctx, "stream", func(s netvuetest.Snapshot) bool { return s.Streaming == 1 && s.VideoFrames >= 5 }); err != nil {
		t.Fatal(err)
	}

	settings := webrtc.SettingEngine{}
	settings.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	settings.SetIncludeLoopbackCandidate(true)
	browser, err := webrtc.NewAPI(webrtc.WithSettingEngine(settings)).NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = browser.Close() })
	var mu sync.Mutex
	var gatheredCandidates []webrtc.ICECandidateInit
	browser.OnICECandidate(func(c *webrtc.ICECandidate) {
		if c != nil {
			mu.Lock()
			gatheredCandidates = append(gatheredCandidates, c.ToJSON())
			mu.Unlock()
		}
	})
	if _, err := browser.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
		t.Fatal(err)
	}
	microphone, err := webrtc.NewTrackLocalStaticSample(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2}, "microphone", "e2e")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := browser.AddTransceiverFromTrack(microphone, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionSendrecv}); err != nil {
		t.Fatal(err)
	}
	connected := make(chan struct{})
	var once sync.Once
	browser.OnConnectionStateChange(func(s webrtc.PeerConnectionState) {
		if s == webrtc.PeerConnectionStateConnected {
			once.Do(func() { close(connected) })
		}
	})
	browser.OnTrack(func(track *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
		for { // drain, so the bridge's RTP is consumed like a real client
			if _, _, err := track.ReadRTP(); err != nil {
				return
			}
		}
	})
	signalWebRTC(t, stack, camera.Host(), browser, func() []webrtc.ICECandidateInit {
		mu.Lock()
		defer mu.Unlock()
		return slices.Clone(gatheredCandidates)
	}, connected)

	// Speak a 700 Hz tone in real time, 20 ms per packet.
	packets := opusTone(t, stack.FFmpeg, 700, 4)
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	for i := 0; i < len(packets); i++ {
		<-ticker.C
		if err := microphone.WriteSample(media.Sample{Data: packets[i], Duration: 20 * time.Millisecond}); err != nil {
			t.Fatal(err)
		}
		if s := camera.State(); s.TalkFrames >= 20 {
			break
		}
	}
	if err := camera.Wait(ctx, "talkback frames", func(s netvuetest.Snapshot) bool { return s.TalkFrames >= 12 }); err != nil {
		t.Fatal(err)
	}
	state := camera.State()
	if !slices.Contains(state.Directions, 3) {
		t.Fatalf("bridge never selected the talkback direction (3): %v", state.Directions)
	}
	if len(state.ProtocolError) != 0 {
		t.Fatalf("protocol errors: %v", state.ProtocolError)
	}

	frames := camera.Talk()
	// Content: valid ADTS at 8 kHz mono that decodes to the spoken tone.
	var adts []byte
	for _, f := range frames {
		adts = append(adts, f.Payload...)
	}
	split, rate, channels, err := netvuetest.SplitADTS(adts)
	if err != nil || len(split) != len(frames) || rate != 8000 || channels != 1 {
		t.Fatalf("talk payloads are not one ADTS frame each at 8 kHz mono: %d/%d frames, %d Hz, %d ch: %v", len(split), len(frames), rate, channels, err)
	}
	command := exec.CommandContext(ctx, stack.FFmpeg, "-hide_banner", "-loglevel", "error", "-f", "aac", "-i", "pipe:0", "-f", "s16le", "-ar", "8000", "-ac", "1", "pipe:1")
	command.Stdin = bytes.NewReader(adts)
	pcm, err := command.Output()
	if err != nil {
		t.Fatalf("decode talkback AAC: %v", err)
	}
	if peak := netvuetest.PeakS16LE(pcm); peak < 1000 {
		t.Fatalf("talkback audio is silent (peak %d)", peak)
	}
	// Every AAC frame must carry the tone, not only the stream on average.
	steady := pcm[len(pcm)/4:]
	for i := 0; i+2048 <= len(steady); i += 2048 {
		if peak := netvuetest.PeakS16LE(steady[i : i+2048]); peak < 1000 {
			t.Fatalf("talkback frame block %d is silent or damaged (peak %d)", i/2048, peak)
		}
	}
	// Skip the AAC priming/Opus ramp-up at the start before measuring pitch.
	if hz := zeroCrossingHz(pcm[len(pcm)/4:], 8000); hz < 600 || hz > 800 {
		t.Fatalf("talkback pitch %.0f Hz, spoken tone is 700 Hz", hz)
	}

	// Cadence: one AAC frame (1024 samples at 8 kHz = 128 ms) per packet, in order.
	var gaps, steps []float64
	for i := 1; i < len(frames); i++ {
		gaps = append(gaps, float64(frames[i].At.Sub(frames[i-1].At))/float64(time.Millisecond))
		step := int64(frames[i].PTS) - int64(frames[i-1].PTS)
		if step <= 0 {
			t.Fatalf("talk PTS not increasing at frame %d: %d -> %d", i, frames[i-1].PTS, frames[i].PTS)
		}
		steps = append(steps, float64(step))
	}
	if m := median(gaps); m < 90 || m > 170 {
		t.Fatalf("median talk packet interval %.0f ms, want about 128 ms", m)
	}
	if m := median(steps); m < 100 || m > 150 {
		t.Fatalf("median talk PTS step %.0f ms, want about 128 ms", m)
	}
}

func zeroCrossingHz(pcm []byte, rate int) float64 {
	crossings, previous, n := 0, int16(0), len(pcm)/2
	for i := 0; i < n; i++ {
		v := int16(uint16(pcm[2*i]) | uint16(pcm[2*i+1])<<8)
		if i > 0 && (previous < 0) != (v < 0) {
			crossings++
		}
		previous = v
	}
	return float64(crossings) / 2 / (float64(n) / float64(rate))
}

func median(values []float64) float64 {
	sorted := slices.Clone(values)
	sort.Float64s(sorted)
	return sorted[len(sorted)/2]
}
