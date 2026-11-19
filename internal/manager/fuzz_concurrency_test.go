package manager

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/mpeg4audio"
	"github.com/mtx26/netvue-ha/internal/netvue"
)

// Close racing with every mutating operation: each caller must get either its
// normal result or a defined sentinel, Close must wait for all runners, and the
// manager must refuse work afterwards.
func TestManagerCloseRacesWithLifecycleOperations(t *testing.T) {
	const cameras = 6
	var active, launched atomic.Int32
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		launched.Add(1)
		active.Add(1)
		defer active.Add(-1)
		<-ctx.Done()
		return nil
	})
	acceptable := func(err error) bool {
		return err == nil || errors.Is(err, ErrManagerClosed) || errors.Is(err, ErrCameraNotFound) ||
			errors.Is(err, ErrCameraConflict) || errors.Is(err, netvue.ErrTalkUnavailable) ||
			errors.Is(err, context.Canceled)
	}
	begin := make(chan struct{})
	var workers sync.WaitGroup
	for i := range cameras {
		host := fmt.Sprintf("192.168.10.%d", 100+i)
		name := fmt.Sprintf("race_%d", i)
		workers.Add(1)
		go func() {
			defer workers.Done()
			<-begin
			ctx := context.Background()
			for range 50 {
				for _, err := range []error{
					func() error { _, _, err := manager.AddCamera(host, name); return err }(),
					func() error { _, err := manager.StopCamera(ctx, host); return err }(),
					func() error { _, _, err := manager.StartCamera(host); return err }(),
					func() error { _, err := manager.OpenTalkback(ctx, host); return err }(),
					func() error { _, err := manager.DeleteCamera(ctx, host); return err }(),
				} {
					if !acceptable(err) {
						t.Errorf("%s: unexpected error %v", host, err)
						return
					}
				}
				_ = manager.Cameras()
			}
		}()
	}
	close(begin)
	closeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	closeResults := make(chan error, 2)
	for range 2 { // Close must be idempotent and safe to call concurrently.
		go func() { closeResults <- manager.Close(closeCtx) }()
	}
	for range 2 {
		if err := <-closeResults; err != nil {
			t.Fatalf("Close() = %v", err)
		}
	}
	workers.Wait()
	if got := active.Load(); got != 0 {
		t.Fatalf("%d sessions still running after Close returned", got)
	}
	if _, _, err := manager.AddCamera("192.168.10.200", "late"); !errors.Is(err, ErrManagerClosed) {
		t.Fatalf("AddCamera after Close = %v, want ErrManagerClosed", err)
	}
	if _, err := manager.OpenTalkback(context.Background(), "192.168.10.100"); !errors.Is(err, ErrManagerClosed) {
		t.Fatalf("OpenTalkback after Close = %v, want ErrManagerClosed", err)
	}
}

// A slow runner must be waited for; the caller's deadline bounds Close without
// abandoning the shutdown itself.
func TestManagerCloseHonorsCallerDeadlineThenCompletes(t *testing.T) {
	release := make(chan struct{})
	started := make(chan struct{})
	manager := newTestManager(t, func(ctx context.Context, _, _, _ string) error {
		close(started)
		<-ctx.Done()
		<-release
		return nil
	})
	if _, _, err := manager.AddCamera("192.168.10.20", "slow"); err != nil {
		t.Fatal(err)
	}
	waitForSignal(t, started, "slow session")
	short, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := manager.Close(short); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Close with a stuck runner = %v, want DeadlineExceeded", err)
	}
	close(release)
	long, cancelLong := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelLong()
	if err := manager.Close(long); err != nil {
		t.Fatalf("Close after the runner returned = %v", err)
	}
}

func FuzzWebRTCSDP(f *testing.F) {
	video := "m=video 9 UDP/TLS/RTP/SAVPF 102\r\nc=IN IP4 0.0.0.0\r\na=rtpmap:102 H264/90000\r\n" +
		"a=fmtp:102 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=64001f\r\na=sendrecv\r\n"
	head := "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
	f.Add(head + video + testAudioSDP("sendrecv")[len(head):])
	f.Add(head + video)
	f.Add(testAudioSDP("recvonly"))
	f.Add(testAudioSDP("inactive"))
	f.Add(head + "m=audio 0 UDP/TLS/RTP/SAVPF 111\r\na=rtpmap:111 opus/48000/2\r\n")
	f.Add("")
	f.Add("m=video")
	sps := []byte{0x67, 0x64, 0x00, 0x1f}
	f.Fuzz(func(t *testing.T, text string) {
		if len(text) > 128<<10 {
			return
		}
		lower := strings.ToLower(text)
		if validateWebRTCSDP(text, sps, true, false) == nil && !strings.Contains(lower, "h264/90000") {
			t.Fatal("SDP without H.264 accepted")
		}
		if microphone, err := offeredMicrophone(text); err == nil && microphone && !strings.Contains(lower, "opus/48000/2") {
			t.Fatal("microphone accepted without Opus")
		}
		if audio, err := offeredCameraAudio(text); err == nil && audio && !strings.Contains(lower, "opus/48000/2") {
			t.Fatal("camera audio accepted without Opus")
		}
		_, _ = answerCanSendAudio(text)
	})
}

func FuzzAACAccessUnitToADTS(f *testing.F) {
	f.Add(48000, uint8(2), []byte{1, 2, 3})
	f.Add(8000, uint8(1), []byte{0})
	f.Add(7350, uint8(7), make([]byte, 8184))
	f.Add(44100, uint8(0), []byte{1})
	f.Add(12345, uint8(2), []byte{})
	f.Fuzz(func(t *testing.T, rate int, channels uint8, unit []byte) {
		audio := &format.MPEG4Audio{Config: &mpeg4audio.AudioSpecificConfig{
			Type: mpeg4audio.ObjectTypeAACLC, SampleRate: rate, ChannelConfig: channels,
		}}
		frame, err := aacAccessUnitToADTS(audio, unit)
		if err != nil {
			return
		}
		if len(frame) != len(unit)+7 || len(frame) > 8191 || frame[0] != 0xff || frame[1]&0xf6 != 0xf0 {
			t.Fatalf("invalid ADTS frame: length %d for a %d-byte unit", len(frame), len(unit))
		}
		if declared := int(frame[3]&3)<<11 | int(frame[4])<<3 | int(frame[5]>>5); declared != len(frame) {
			t.Fatalf("ADTS length field %d, frame length %d", declared, len(frame))
		}
		if got := int(frame[2]&1)<<2 | int(frame[3]>>6); got != int(channels) {
			t.Fatalf("ADTS channel config %d, want %d", got, channels)
		}
		if aacSampleRates[(frame[2]>>2)&15] != rate || string(frame[7:]) != string(unit) {
			t.Fatal("ADTS sample rate or payload does not round-trip")
		}
	})
}
