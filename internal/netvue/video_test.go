package netvue

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net"
	"testing"
	"time"

	"github.com/bluenviron/mediacommon/v2/pkg/codecs/h264"
)

func TestPrependParametersMatchesCurrentParameterSets(t *testing.T) {
	accessUnit := h264.AnnexB{
		[]byte{0x67, 1},
		[]byte{0x65, 3},
	}
	got := prependParameters(accessUnit, []byte{0x67, 2}, []byte{0x68, 2})
	if len(got) != 4 || got[0][1] != 2 || got[1][1] != 2 {
		t.Fatalf("current SPS/PPS were not prepended: %#v", got)
	}
}

func TestPublisherRejectsConflictingParameterSetsWithoutChangingState(t *testing.T) {
	oldSPS := []byte{0x67, 0x42, 0, 0x1e, 0xf4, 0x21, 0x32}
	oldPPS := []byte{0x68, 0xc0}
	publisher := &videoPublisher{
		sps:     append([]byte(nil), oldSPS...),
		pps:     append([]byte(nil), oldPPS...),
		started: true,
	}
	accessUnit := []byte{
		0, 0, 0, 1, 0x67, 0x42, 0, 0x1e, 0xf4, 0x10, 0x4c, 0x80,
		0, 0, 0, 1, 0x68, 0xd0,
		0, 0, 0, 1, 0x68, 0xe0,
	}
	err := publisher.Push(accessUnit, 1)
	if !errors.Is(err, ErrProtocol) {
		t.Fatalf("Push() error = %v, want ErrProtocol", err)
	}
	if !bytes.Equal(publisher.sps, oldSPS) || !bytes.Equal(publisher.pps, oldPPS) {
		t.Fatalf("rejected access unit changed stored parameters: SPS=%x PPS=%x", publisher.sps, publisher.pps)
	}
	if !publisher.started {
		t.Fatal("rejected access unit changed publication state")
	}
}

func TestPublisherTracksRepeatedParameterAndResolutionChanges(t *testing.T) {
	tests := []struct {
		width, height int
		sps, pps      []byte
	}{
		{64, 64, []byte{0x67, 0x42, 0, 0x1e, 0xf4, 0x21, 0x32}, []byte{0x68, 0xc0}},
		{128, 64, []byte{0x67, 0x42, 0, 0x1e, 0xf4, 0x10, 0x4c, 0x80}, []byte{0x68, 0xd0}},
		{64, 128, []byte{0x67, 0x42, 0, 0x1e, 0xf4, 0x20, 0x8c, 0x80}, []byte{0x68, 0xe0}},
	}
	publisher := &videoPublisher{}
	for index, test := range tests {
		accessUnit := append([]byte{0, 0, 0, 1}, test.sps...)
		accessUnit = append(accessUnit, 0, 0, 0, 1)
		accessUnit = append(accessUnit, test.pps...)
		if err := publisher.Push(accessUnit, uint32(index*33)); err != nil {
			t.Fatalf("Push() with %dx%d SPS/PPS: %v", test.width, test.height, err)
		}
		var parsed h264.SPS
		if err := parsed.Unmarshal(publisher.sps); err != nil {
			t.Fatalf("stored SPS for %dx%d is invalid: %v", test.width, test.height, err)
		}
		if parsed.Width() != test.width || parsed.Height() != test.height ||
			!bytes.Equal(publisher.pps, test.pps) {
			t.Fatalf("stored parameters = %dx%d / %x, want %dx%d / %x",
				parsed.Width(), parsed.Height(), publisher.pps, test.width, test.height, test.pps)
		}
	}
}

func TestTimestampClockRecoversCollisionsAndResets(t *testing.T) {
	var clock timestampClock
	for _, step := range []struct {
		value uint32
		want  uint32
	}{
		{value: 1_000, want: 0},
		{value: 1_033, want: 2_970},
		{value: 1_033, want: 3_060},
		{value: 1_033, want: 3_150},
		{value: 0, want: 3_240},
		{value: 17, want: 4_770},
		{value: 10_017, want: 904_770},
		{value: 20_018, want: 904_860},
		{value: 20_019, want: 904_950},
	} {
		if got := clock.update(step.value); got != step.want {
			t.Fatalf("update(%d) = %d RTP ticks, want %d", step.value, got, step.want)
		}
	}
}

func TestTimestampClockPreservesNaturalWrapInRTPDomain(t *testing.T) {
	var clock timestampClock
	base := uint32(0xfffffff0)
	first := base + clock.update(0xffffff00)
	second := base + clock.update(0)
	if delta := second - first; delta != 256*90 {
		t.Fatalf("RTP timestamp delta across source wrap = %d, want %d", delta, 256*90)
	}
}

func TestValidateH264SPSRejectsDisplayAxesOverLimit(t *testing.T) {
	tests := []struct {
		name string
		sps  h264.SPS
	}{
		{
			name: "width",
			sps: h264.SPS{
				PicWidthInMbsMinus1:       240,
				PicHeightInMapUnitsMinus1: 131,
				FrameMbsOnlyFlag:          true,
			},
		},
		{
			name: "height",
			sps: h264.SPS{
				PicWidthInMbsMinus1:       9,
				PicHeightInMapUnitsMinus1: 135,
				FrameMbsOnlyFlag:          true,
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := validateH264SPS(test.sps); !errors.Is(err, ErrProtocol) {
				t.Fatalf("validateH264SPS() error = %v, want ErrProtocol", err)
			}
		})
	}
}

func TestValidateLocalRTSPURL(t *testing.T) {
	for _, value := range []string{
		"rtsp://127.0.0.1:8554/netvue",
		"rtsp://[::1]:8554/netvue",
	} {
		if err := validateLocalRTSPURL(value); err != nil {
			t.Errorf("rejected %q: %v", value, err)
		}
	}
	for _, value := range []string{
		"rtsp://192.168.1.5:8554/netvue",
		"rtsp://127.0.0.1/netvue",
		"rtsp://user:pass@127.0.0.1:8554/netvue",
		"rtsp://127.0.0.1:8554/",
		"rtsp://127.0.0.1:8554/netvue?token=fake",
	} {
		if err := validateLocalRTSPURL(value); err == nil {
			t.Errorf("accepted unsafe URL %q", value)
		}
	}
}

func TestPrependParametersWithoutLosingAccessUnit(t *testing.T) {
	sps := []byte{byte(h264.NALUTypeSPS), 1}
	pps := []byte{byte(h264.NALUTypePPS), 2}
	idr := []byte{byte(h264.NALUTypeIDR), 3}
	got := prependParameters(h264.AnnexB{idr}, sps, pps)
	if len(got) != 3 || !bytes.Equal(got[0], sps) ||
		!bytes.Equal(got[1], pps) || !bytes.Equal(got[2], idr) {
		t.Fatalf("unexpected access unit: %x", got)
	}
}

func TestPublisherRejectsMalformedAnnexB(t *testing.T) {
	publisher, err := newVideoPublisher(context.Background(), "rtsp://127.0.0.1:8554/netvue")
	if err != nil {
		t.Fatal(err)
	}
	defer publisher.Close()
	for _, test := range []struct {
		accessUnit []byte
		want       string
	}{
		{[]byte{1, 2, 3}, "Invalid H.264 data [H.264]: missing Annex B initial delimiter"},
		{[]byte{0, 0, 1}, "Invalid H.264 data [H.264]: Annex B access unit contains no NAL units"},
	} {
		err := publisher.Push(test.accessUnit, 1)
		if err == nil {
			t.Fatal("malformed Annex B accepted")
		}
		err = atStage("H.264", err)
		if !errors.Is(err, ErrProtocol) || SessionDiagnostic(err) != test.want {
			t.Fatalf("diagnostic = %q, error = %v, want %q", SessionDiagnostic(err), err, test.want)
		}
	}
}

func TestValidateH264SPSChecksCodedAndDisplayedBounds(t *testing.T) {
	valid4K := h264.SPS{
		ChromaFormatIdc:           1,
		PicWidthInMbsMinus1:       239,
		PicHeightInMapUnitsMinus1: 67,
		FrameMbsOnlyFlag:          false,
		FrameCropping:             &h264.SPS_FrameCropping{BottomOffset: 4},
	}
	if err := validateH264SPS(valid4K); err != nil {
		t.Fatalf("rejected valid cropped 4K coded dimensions: %v", err)
	}

	oversizedCoded := valid4K
	oversizedCoded.PicWidthInMbsMinus1 = 255
	oversizedCoded.FrameCropping = &h264.SPS_FrameCropping{RightOffset: 128, BottomOffset: 4}
	if err := validateH264SPS(oversizedCoded); !errors.Is(err, ErrProtocol) {
		t.Fatalf("expected oversized coded dimensions to be rejected, got %v", err)
	}

	overflowing := valid4K
	overflowing.PicWidthInMbsMinus1 = ^uint32(0)
	if err := validateH264SPS(overflowing); !errors.Is(err, ErrProtocol) {
		t.Fatalf("expected overflowing coded dimensions to be rejected, got %v", err)
	}

	invalidCrop := valid4K
	invalidCrop.FrameCropping = &h264.SPS_FrameCropping{BottomOffset: ^uint32(0)}
	if err := validateH264SPS(invalidCrop); !errors.Is(err, ErrProtocol) {
		t.Fatalf("expected invalid frame cropping to be rejected, got %v", err)
	}
}

func TestCheckRTSPListenerSupportsExpandedIPv6Loopback(t *testing.T) {
	listener, err := net.Listen("tcp6", "[::1]:0")
	if err != nil {
		t.Skipf("IPv6 loopback is unavailable: %v", err)
	}
	defer listener.Close()
	port := listener.Addr().(*net.TCPAddr).Port
	value := fmt.Sprintf("rtsp://[0:0:0:0:0:0:0:1]:%d/netvue", port)
	if err := checkRTSPListener(context.Background(), value); err != nil {
		t.Fatalf("expanded IPv6 loopback was not reachable: %v", err)
	}
}

func TestPublisherStartStopsWhenContextIsCancelled(t *testing.T) {
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	publisher, err := newVideoPublisher(ctx, fmt.Sprintf("rtsp://127.0.0.1:%d/netvue",
		listener.Addr().(*net.TCPAddr).Port))
	if err != nil {
		t.Fatal(err)
	}
	defer publisher.Close()
	publisher.sps = []byte{0x67, 0x42, 0x00, 0x1e}
	publisher.pps = []byte{0x68, 0x00}

	accepted := make(chan net.Conn, 1)
	go func() {
		conn, err := listener.Accept()
		if err == nil {
			accepted <- conn
		}
	}()
	started := make(chan error, 1)
	go func() {
		started <- publisher.start()
	}()

	var serverConn net.Conn
	select {
	case serverConn = <-accepted:
		defer serverConn.Close()
	case <-time.After(time.Second):
		t.Fatal("RTSP client did not connect")
	}
	cancel()
	select {
	case err := <-started:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("expected context cancellation, got %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("RTSP startup did not stop after context cancellation")
	}
}

func TestPublisherStartHonorsVideoStartupDeadline(t *testing.T) {
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()

	publisher, err := newVideoPublisher(context.Background(), fmt.Sprintf(
		"rtsp://127.0.0.1:%d/netvue", listener.Addr().(*net.TCPAddr).Port))
	if err != nil {
		t.Fatal(err)
	}
	defer publisher.Close()
	publisher.sps = []byte{0x67, 0x42, 0x00, 0x1e}
	publisher.pps = []byte{0x68, 0x00}
	publisher.SetStartupDeadline(time.Now().Add(100 * time.Millisecond))

	accepted := make(chan net.Conn, 1)
	go func() {
		conn, err := listener.Accept()
		if err == nil {
			accepted <- conn
		}
	}()
	started := make(chan error, 1)
	go func() {
		started <- publisher.start()
	}()

	select {
	case conn := <-accepted:
		defer conn.Close()
	case <-time.After(time.Second):
		t.Fatal("RTSP client did not connect")
	}
	select {
	case err := <-started:
		if !errors.Is(err, ErrVideoStartup) {
			t.Fatalf("expected RTSP startup deadline error, got %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("RTSP setup exceeded the video startup deadline")
	}
}
