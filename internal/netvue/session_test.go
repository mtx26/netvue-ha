package netvue

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/mtx26/netvue-ha/internal/netvue/netvuetest"
)

func TestSessionDiscoveryCallbackReceivesMetadataBeforeAuthentication(t *testing.T) {
	signal := -39
	camera := netvuetest.Start(t, netvuetest.Options{
		Host:            netvuetest.AllocHost(45),
		SerialNumber:    "callback-serial",
		FirmwareVersion: "callback-fw",
		WiFiSSID:        "callback-network",
		WiFiSignalLevel: &signal,
		RejectLogins:    1,
	})
	rtspListener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = rtspListener.Close() })

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	t.Cleanup(cancel)
	var discovered DiscoveryResult
	err = runSessionWithLiveTalkAndDiscovery(
		ctx,
		"127.0.0.1",
		camera.Host(),
		fmt.Sprintf("rtsp://%s/metadata", rtspListener.Addr()),
		0,
		nil,
		nil,
		func(result DiscoveryResult) { discovered = result },
	)
	if !errors.Is(err, ErrAuthenticationRejected) {
		t.Fatalf("session error = %v, want simulated authentication rejection", err)
	}
	if discovered.Host != camera.Host() || discovered.ConnectionPort != camera.Port() ||
		discovered.Metadata == nil ||
		discovered.Metadata.SerialNumber != "callback-serial" ||
		discovered.Metadata.FirmwareVersion != "callback-fw" ||
		discovered.Metadata.WiFiSSID != "callback-network" ||
		discovered.Metadata.WiFiSignalLevel == nil ||
		*discovered.Metadata.WiFiSignalLevel != signal {
		t.Fatalf("discovery callback result = %+v", discovered)
	}
	if got := camera.State().LiveRequests; got != 0 {
		t.Fatalf("LIVE started after rejected login: requests = %d", got)
	}
}

func TestLiveRequestIsOneExplicitCommand(t *testing.T) {
	client, server := testPipe(t)
	result := make(chan error, 1)
	go func() {
		packet, err := ReadPacket(server)
		if err == nil && packet.Kind != packetCommand {
			err = ErrProtocol
		}
		if err == nil {
			var units []map[string]json.RawMessage
			err = json.Unmarshal(packet.Body, &units)
			if err == nil && len(units) != 1 {
				err = ErrProtocol
			}
			if err == nil {
				var command, version int
				if json.Unmarshal(units[0]["Q"], &command) != nil ||
					json.Unmarshal(units[0]["V"], &version) != nil ||
					command != 3 || version != 2 {
					err = ErrProtocol
				}
			}
		}
		result <- err
	}()
	if err := sendLiveRequest(context.Background(), client); err != nil {
		t.Fatal(err)
	}
	if err := <-result; err != nil {
		t.Fatal(err)
	}
}

func TestLiveRequestCanSelectAudioAndVideo(t *testing.T) {
	client, server := testPipe(t)
	result := make(chan [][]int, 1)
	go func() {
		packet, err := ReadPacket(server)
		if err != nil {
			result <- nil
			return
		}
		units, err := ParseUnits(packet.Body)
		if err != nil || len(units) != 1 {
			result <- nil
			return
		}
		var request []json.RawMessage
		if json.Unmarshal(units[0]["P"], &request) != nil || len(request) != 3 {
			result <- nil
			return
		}
		var channels [][]int
		if json.Unmarshal(request[1], &channels) != nil {
			result <- nil
			return
		}
		result <- channels
	}()
	if err := sendLiveRequestChannels(context.Background(), client, true); err != nil {
		t.Fatal(err)
	}
	if got := <-result; len(got) != 2 || got[0][0] != 1 || got[1][0] != 2 {
		t.Fatalf("selected channels = %v, want video and audio", got)
	}
}

func TestReceiveLiveRequestsReceiveOnlyAudioForSelectedChannel(t *testing.T) {
	client, server := testPipe(t)
	publisher := &audioTestPublisher{}
	result := make(chan error, 1)
	go func() {
		ack, err := EncodeCommand([]map[string]any{{
			"A": 3, "V": 1, "C": 0,
			"P": [][]any{
				{0, 1, 1, []any{}},
				{1, 1, 2, []any{8000, 1, 1}},
			},
		}})
		if err == nil {
			err = writeFull(server, ack)
		}
		if err == nil {
			var request Packet
			request, err = ReadPacket(server)
			if err == nil {
				var units []map[string]json.RawMessage
				units, err = ParseUnits(request.Body)
				if err == nil && (len(units) != 1 || !commandHasValues(units[0], 5, 1, []int{1})) {
					err = errors.New("audio direction request was not receive-only")
				}
			}
		}
		if err == nil {
			err = writeFull(server, audioTestPacket(false, 40, []byte{1, 2, 3}))
		}
		if err == nil {
			err = writeFull(server, mediaPacket(1, []byte{1}))
		}
		result <- err
	}()
	ctx, cancel := context.WithTimeoutCause(context.Background(), 100*time.Millisecond, errSessionDurationElapsed)
	defer cancel()
	err := receiveLiveWithTimeoutsAndAudio(ctx, client, publisher, time.Second, time.Second, true)
	if err != nil {
		t.Fatalf("receive LIVE with selected audio: %v", err)
	}
	if err := <-result; err != nil {
		t.Fatal(err)
	}
	if len(publisher.audioFrames) != 1 || publisher.audioFrames[0].timestamp != 40 ||
		publisher.audioParameters[0] != (audioParameters{sampleRate: 8000, sampleSize: 1, channels: 1}) {
		t.Fatalf("audio frames/parameters = %+v / %+v", publisher.audioFrames, publisher.audioParameters)
	}
}

func TestReceiveLiveContinuesVideoWithUnsupportedAudioParameters(t *testing.T) {
	client, server := testPipe(t)
	publisher := &audioTestPublisher{}
	result := make(chan error, 1)
	go func() {
		ack, err := EncodeCommand([]map[string]any{{
			"A": 3, "V": 1, "C": 0,
			"P": [][]any{
				{0, 1, 1, []any{}},
				{1, 1, 2, []any{96000, 16, 1}},
			},
		}})
		if err == nil {
			err = writeFull(server, ack)
		}
		if err == nil {
			err = writeFull(server, mediaPacket(0, []byte{1}))
		}
		if err == nil {
			err = writeFull(server, audioTestPacket(false, 40, []byte{1, 2, 3}))
		}
		result <- err
	}()
	ctx, cancel := context.WithCancelCause(context.Background())
	defer cancel(context.Canceled)
	received := make(chan error, 1)
	go func() {
		received <- receiveLiveWithTimeoutsAndAudio(ctx, client, publisher, 5*time.Second, 5*time.Second, true)
	}()
	select {
	case err := <-result:
		if err != nil {
			t.Fatal(err)
		}
	case err := <-received:
		t.Fatalf("LIVE stopped before packets were sent: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("timed out sending LIVE packets")
	}
	// The following audio packet has been read, so the preceding video push
	// has completed. End the bounded session without a scheduler-sensitive 100ms timer.
	cancel(errSessionDurationElapsed)
	if err := <-received; err != nil {
		t.Fatalf("receive LIVE with unsupported audio parameters: %v", err)
	}
	if publisher.CurrentPublicationFrames() == 0 || len(publisher.audioFrames) != 0 {
		t.Fatalf("video frames=%d, audio frames=%d; want video and no audio",
			publisher.CurrentPublicationFrames(), len(publisher.audioFrames))
	}
}

func TestReceiveLiveRestartsVideoAfterLateAudioTrackConfiguration(t *testing.T) {
	client, server := testPipe(t)
	publisher := &audioTestPublisher{resetVideo: true}
	result := make(chan error, 1)
	go func() {
		ack, err := EncodeCommand([]map[string]any{{
			"A": 3, "V": 1, "C": 0,
			"P": [][]any{{1, 1, 2, []any{16000, 16, 1}}},
		}})
		if err == nil {
			err = writeFull(server, ack)
		}
		if err == nil {
			var request Packet
			request, err = ReadPacket(server)
			if err == nil {
				units, parseErr := ParseUnits(request.Body)
				if parseErr != nil || len(units) != 1 ||
					!commandHasValues(units[0], 5, 1, []int{1}) {
					err = errors.New("audio direction request was not receive-only")
				}
			}
		}
		if err == nil {
			for _, packet := range [][]byte{
				mediaPacket(0, []byte{1}),
				mediaPacket(33, []byte{3}),
				audioTestPacket(false, 40, []byte{1, 2, 3}),
				mediaPacket(66, []byte{2}),
			} {
				if err = writeFull(server, packet); err != nil {
					break
				}
			}
		}
		result <- err
	}()
	ctx, cancel := context.WithTimeoutCause(context.Background(), 100*time.Millisecond, errSessionDurationElapsed)
	defer cancel()
	err := receiveLiveWithTimeoutsAndAudio(ctx, client, publisher, time.Second, time.Second, true)
	if err != nil {
		t.Fatalf("receive LIVE after late audio configuration: %v", err)
	}
	if err := <-result; err != nil {
		t.Fatal(err)
	}
	if publisher.Generation() != 2 || publisher.CurrentPublicationFrames() != 1 {
		t.Fatalf("video publication did not restart after audio setup: generation=%d current=%d",
			publisher.Generation(), publisher.CurrentPublicationFrames())
	}
}

func TestReceiveLiveUnsupportedAudioDoesNotStopVideo(t *testing.T) {
	client, server := testPipe(t)
	publisher := &audioTestPublisher{audioErr: ErrAudioFrame}
	result := make(chan error, 1)
	go func() {
		ack, err := EncodeCommand([]map[string]any{{
			"A": 3, "V": 1, "C": 0,
			"P": [][]any{{1, 1, 2, []any{8000, 8, 1}}},
		}})
		if err == nil {
			err = writeFull(server, ack)
		}
		if err == nil {
			var request Packet
			request, err = ReadPacket(server)
			if err == nil {
				units, parseErr := ParseUnits(request.Body)
				if parseErr != nil || len(units) != 1 ||
					!commandHasValues(units[0], 5, 1, []int{1}) {
					err = errors.New("audio direction request was not receive-only")
				}
			}
		}
		if err == nil {
			for _, packet := range [][]byte{
				mediaPacket(0, []byte{1}),
				audioTestPacket(false, 40, []byte{0xff}),
				audioTestPacket(false, 50, []byte{0xfe}),
				mediaPacket(66, []byte{2}),
			} {
				if err = writeFull(server, packet); err != nil {
					break
				}
			}
		}
		result <- err
	}()
	ctx, cancel := context.WithTimeoutCause(context.Background(), 100*time.Millisecond, errSessionDurationElapsed)
	defer cancel()
	err := receiveLiveWithTimeoutsAndAudio(ctx, client, publisher, time.Second, time.Second, true)
	if err != nil {
		t.Fatalf("unsupported audio interrupted video: %v", err)
	}
	if err := <-result; err != nil {
		t.Fatal(err)
	}
	if publisher.CurrentPublicationFrames() == 0 {
		t.Fatal("video was not kept active after unsupported audio")
	}
	if len(publisher.audioFrames) != 1 {
		t.Fatalf("audio frames processed after disabling the track = %d, want 1", len(publisher.audioFrames))
	}
}

func TestReceiveLiveKeepsVideoAfterRealOversizedAudioError(t *testing.T) {
	client, server := testPipe(t)
	publisher := &realAudioTestPublisher{secondVideo: make(chan struct{})}
	ctx, cancel := context.WithCancelCause(context.Background())
	defer cancel(context.Canceled)
	received := make(chan error, 1)
	go func() {
		received <- receiveLiveWithTimeoutsAndAudio(ctx, client, publisher, 5*time.Second, 5*time.Second, true)
	}()
	sent := make(chan error, 1)
	go func() {
		ack, err := EncodeCommand([]map[string]any{{
			"A": 3, "V": 1, "C": 0,
			"P": [][]any{{1, 1, 2, []any{8000, 1, 1}}},
		}})
		if err == nil {
			err = writeFull(server, ack)
		}
		if err == nil {
			var request Packet
			request, err = ReadPacket(server)
			if err == nil {
				units, parseErr := ParseUnits(request.Body)
				if parseErr != nil || len(units) != 1 || !commandHasValues(units[0], 5, 1, []int{1}) {
					err = errors.New("audio direction request was not receive-only")
				}
			}
		}
		if err == nil {
			for _, packet := range [][]byte{
				mediaPacket(0, []byte{1}),
				audioTestPacket(false, 40, make([]byte, audioMaxSize+1)),
				audioTestPacket(false, 50, []byte{0xff}),
				mediaPacket(66, []byte{2}),
			} {
				if err = writeFull(server, packet); err != nil {
					break
				}
			}
		}
		sent <- err
	}()
	select {
	case err := <-sent:
		if err != nil {
			t.Fatal(err)
		}
	case err := <-received:
		t.Fatalf("LIVE stopped before the second video frame: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("timed out sending LIVE packets")
	}
	select {
	case <-publisher.secondVideo:
	case err := <-received:
		t.Fatalf("LIVE stopped before processing the second video frame: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("timed out waiting for the second video frame")
	}
	cancel(errSessionDurationElapsed)
	if err := <-received; err != nil {
		t.Fatalf("oversized audio interrupted video: %v", err)
	}
	if publisher.audioCalls != 1 || !errors.Is(publisher.audioError, ErrAudioFrame) {
		t.Fatalf("audio calls=%d, error=%v; want one real ErrAudioFrame",
			publisher.audioCalls, publisher.audioError)
	}
	if publisher.Generation() != 2 || publisher.CurrentPublicationFrames() != 1 {
		t.Fatalf("video did not continue after audio error: generation=%d frames=%d",
			publisher.Generation(), publisher.CurrentPublicationFrames())
	}
}

func TestAudioDiagnosticIsActionable(t *testing.T) {
	err := atStage("audio", withReason("unsupported audio codec", ErrAudioFrame))
	const want = "Unsupported or malformed Netvue audio [audio]: unsupported audio codec"
	if got := SessionDiagnostic(err); got != want {
		t.Fatalf("audio diagnostic = %q, want %q", got, want)
	}

	err = atStage(
		"audio parameters",
		withReason("audio channel parameters out of range", errors.Join(ErrProtocol, ErrUnsupportedAudioParameters)),
	)
	const wantParameters = "Unsupported Netvue audio parameters [audio parameters]: audio channel parameters out of range"
	if got := SessionDiagnostic(err); got != wantParameters {
		t.Fatalf("audio parameter diagnostic = %q, want %q", got, wantParameters)
	}
}

func TestReceiveLiveFallsBackToVideoWhenAudioSelectionIsRejected(t *testing.T) {
	client, server := testPipe(t)
	publisher := &generationTestPublisher{}
	rejected := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 1}})
	accepted := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 1, "C": 0}})
	result := make(chan error, 1)
	go func() {
		if err := writeFull(server, rejected); err != nil {
			result <- err
			return
		}
		fallback, err := ReadPacket(server)
		if err == nil {
			units, parseErr := ParseUnits(fallback.Body)
			if parseErr != nil || len(units) != 1 {
				err = errors.New("invalid video-only fallback request")
			} else {
				var selection [][]int
				var params []json.RawMessage
				if json.Unmarshal(units[0]["P"], &params) != nil || len(params) != 3 ||
					json.Unmarshal(params[1], &selection) != nil || len(selection) != 1 ||
					selection[0][0] != 1 {
					err = errors.New("audio fallback did not select video only")
				}
			}
		}
		if err == nil {
			err = writeFull(server, accepted)
		}
		if err == nil {
			err = writeFull(server, mediaPacket(1, []byte{1}))
		}
		result <- err
	}()
	ctx, cancel := context.WithTimeoutCause(context.Background(), 100*time.Millisecond, errSessionDurationElapsed)
	defer cancel()
	if err := receiveLiveWithTimeoutsAndAudio(ctx, client, publisher, time.Second, time.Second, true); err != nil {
		t.Fatalf("receive LIVE after video-only fallback: %v", err)
	}
	if err := <-result; err != nil {
		t.Fatal(err)
	}
	if publisher.CurrentPublicationFrames() == 0 {
		t.Fatal("video was not published after the audio fallback")
	}
}

func TestReceiveLiveRejectsMediaBeforeConfirmation(t *testing.T) {
	client, server := testPipe(t)
	publisher, err := newVideoPublisher(context.Background(), "rtsp://127.0.0.1:8554/netvue")
	if err != nil {
		t.Fatal(err)
	}
	defer publisher.Close()
	go func() {
		_ = writeFull(server, makePacket(6, 0, []byte{0x10, 0, 0, 0, 0, 0x67}))
	}()
	err = receiveLive(contextWithTimeout(t), client, publisher)
	if err == nil || !errors.Is(err, ErrProtocol) {
		t.Fatalf("expected media-before-confirmation error, got %v", err)
	}
}

func TestReceiveLiveDiagnostics(t *testing.T) {
	tests := []struct {
		name       string
		packets    [][]byte
		closeAfter bool
		want       string
		wantError  error
	}{
		{
			name:      "protocol header",
			packets:   [][]byte{makePacket(1, 1, []byte{0})},
			want:      "Invalid Netvue protocol data [LIVE packet]: unsupported packet flags",
			wantError: ErrProtocol,
		},
		{
			name:      "malformed command",
			packets:   [][]byte{makePacket(7, 0, []byte(`secret!`))},
			want:      "Invalid Netvue protocol data [LIVE response]: invalid command JSON",
			wantError: ErrProtocol,
		},
		{
			name:      "media before confirmation",
			packets:   [][]byte{mediaPacket(1, []byte{1})},
			want:      "Invalid Netvue protocol data [LIVE response]: media before confirmation",
			wantError: ErrProtocol,
		},
		{
			name:      "invalid media",
			packets:   [][]byte{liveConfirmationPacket(t), makePacketKind(6, 0, packetMedia, []byte{0xf0, 0, 0, 0, 0, 1})},
			want:      "Invalid Netvue media frame [media frame]: unsupported frame type",
			wantError: ErrMediaFrame,
		},
		{
			name:      "invalid H264",
			packets:   [][]byte{liveConfirmationPacket(t), mediaPacket(1, []byte("secret"))},
			want:      "Invalid H.264 data [H.264]: missing Annex B initial delimiter",
			wantError: ErrProtocol,
		},
		{
			name:       "closed connection",
			packets:    [][]byte{{1}},
			closeAfter: true,
			want:       "Connection error [LIVE packet]",
			wantError:  ErrConnectionClosed,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			client, server := testPipe(t)
			publisher, err := newVideoPublisher(context.Background(), "rtsp://127.0.0.1:8554/netvue")
			if err != nil {
				t.Fatal(err)
			}
			defer publisher.Close()
			go func() {
				for _, packet := range test.packets {
					if writeFull(server, packet) != nil {
						return
					}
				}
				if test.closeAfter {
					_ = server.Close()
				}
			}()
			err = receiveLive(contextWithTimeout(t), client, publisher)
			if !errors.Is(err, test.wantError) {
				t.Fatalf("error = %v, want %v", err, test.wantError)
			}
			if got := SessionDiagnostic(err); got != test.want || strings.Contains(got, "secret") {
				t.Fatalf("diagnostic = %q, want %q", got, test.want)
			}
			if retryableSessionError(err) != test.closeAfter {
				t.Fatalf("unexpected retry policy for %v", err)
			}
		})
	}
}

func liveConfirmationPacket(t *testing.T) []byte {
	t.Helper()
	packet, err := EncodeCommand([]map[string]any{{"A": 3, "V": 1, "C": 0}})
	if err != nil {
		t.Fatal(err)
	}
	return packet
}

func commandHasValues(unit map[string]json.RawMessage, action, version int, params []int) bool {
	var gotAction, gotVersion int
	var rawParams []int
	return json.Unmarshal(unit["Q"], &gotAction) == nil && gotAction == action &&
		json.Unmarshal(unit["V"], &gotVersion) == nil && gotVersion == version &&
		json.Unmarshal(unit["P"], &rawParams) == nil &&
		rawParamsEqual(rawParams, params)
}

func rawParamsEqual(left, right []int) bool {
	if len(left) != len(right) {
		return false
	}
	for i := range left {
		if left[i] != right[i] {
			return false
		}
	}
	return true
}

func TestReceiveLiveConfirmationVersions(t *testing.T) {
	tests := []struct {
		name        string
		unit        map[string]any
		wantSuccess bool
		wantVersion bool
		diagnostic  string
	}{
		{name: "version one accepted", unit: map[string]any{"A": 3, "V": 1, "C": 0}, wantSuccess: true},
		{name: "version two accepted", unit: map[string]any{"A": 3, "V": 2, "C": 0}, wantSuccess: true},
		{name: "missing result rejected", unit: map[string]any{"A": 3, "V": 1}},
		{name: "null result rejected", unit: map[string]any{"A": 3, "V": 1, "C": nil}, diagnostic: "Invalid Netvue protocol data [LIVE response]: missing or invalid result"},
		{name: "failure result rejected", unit: map[string]any{"A": 3, "V": 1, "C": 1}},
		{name: "unknown version rejected", unit: map[string]any{"A": 3, "V": 3, "C": 0}, wantVersion: true},
		{name: "missing version rejected", unit: map[string]any{"A": 3, "C": 0}, wantVersion: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			client, server := testPipe(t)
			publisher := &generationTestPublisher{}
			ack := mustEncodeCommand(t, []map[string]any{test.unit})
			sent := make(chan error, 1)
			go func() {
				err := writeFull(server, ack)
				if err == nil && test.wantSuccess {
					err = writeFull(server, mediaPacket(1, []byte{1}))
				}
				sent <- err
			}()
			ctx, cancel := context.WithTimeoutCause(context.Background(), 100*time.Millisecond, errSessionDurationElapsed)
			defer cancel()
			err := receiveLive(ctx, client, publisher)
			if test.wantSuccess {
				if err != nil || publisher.CurrentPublicationFrames() == 0 {
					t.Fatalf("valid LIVE confirmation did not publish video: %v", err)
				}
			} else if !errors.Is(err, ErrProtocol) || errors.Is(err, ErrLiveResponseVersion) != test.wantVersion {
				t.Fatalf("unexpected LIVE response error: %v", err)
			}
			if !test.wantSuccess && publisher.CurrentPublicationFrames() != 0 {
				t.Fatal("invalid LIVE confirmation published video")
			}
			if test.diagnostic != "" && SessionDiagnostic(err) != test.diagnostic {
				t.Fatalf("diagnostic = %q, want %q", SessionDiagnostic(err), test.diagnostic)
			}
			if err := <-sent; err != nil {
				t.Fatalf("server could not send LIVE response: %v", err)
			}
		})
	}
}

func contextWithTimeout(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	t.Cleanup(cancel)
	return ctx
}

func TestReceiveLiveStartupDeadlineIsNotExtendedByCommands(t *testing.T) {
	client, server := testPipe(t)
	command, err := EncodeCommand([]map[string]any{{"A": 2, "V": 2, "C": 0}})
	if err != nil {
		t.Fatal(err)
	}
	go func() {
		ticker := time.NewTicker(10 * time.Millisecond)
		defer ticker.Stop()
		for range ticker.C {
			if err := writeFull(server, command); err != nil {
				return
			}
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	started := time.Now()
	err = receiveLiveWithTimeouts(ctx, client, &generationTestPublisher{},
		80*time.Millisecond, time.Second)
	if !errors.Is(err, ErrVideoNotReady) {
		t.Fatalf("expected absolute video startup timeout, got %v", err)
	}
	if elapsed := time.Since(started); elapsed > 500*time.Millisecond {
		t.Fatalf("commands prolonged video startup for %v", elapsed)
	}
}

func TestReceiveLiveCancellationWakesBlockedRead(t *testing.T) {
	client, server := testPipe(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	publisher := &generationTestPublisher{total: 1, current: 1, generation: 1, pushed: make(chan struct{})}
	acknowledgment := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 2, "C": 0}})
	serverDone := make(chan error, 1)
	go func() {
		if err := writeFull(server, acknowledgment); err != nil {
			serverDone <- err
			return
		}
		serverDone <- writeFull(server, mediaPacket(1, []byte{1}))
	}()
	result := make(chan error, 1)
	go func() {
		result <- receiveLive(ctx, client, publisher)
	}()
	select {
	case <-publisher.pushed:
	case <-time.After(time.Second):
		t.Fatal("live frame was not processed")
	}
	cancel()
	select {
	case err := <-result:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("expected caller cancellation, got %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("blocked LIVE read did not stop after cancellation")
	}
	if err := <-serverDone; err != nil {
		t.Fatalf("server could not send LIVE packets: %v", err)
	}
}

func TestReceiveLiveHandlesVideoPacketSplitAcrossDelayedWrites(t *testing.T) {
	client, server := testPipe(t)
	publisher := &generationTestPublisher{pushed: make(chan struct{})}
	ack := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 2, "C": 0}})
	packet := mediaPacket(1, []byte{1})
	serverDone := make(chan error, 1)
	go func() {
		if err := writeFull(server, ack); err != nil {
			serverDone <- err
			return
		}
		start := 0
		for _, end := range []int{3, 11, len(packet)} {
			if err := writeFull(server, packet[start:end]); err != nil {
				serverDone <- err
				return
			}
			start = end
			if end < len(packet) {
				time.Sleep(10 * time.Millisecond)
			}
		}
		serverDone <- nil
	}()

	ctx, cancel := context.WithCancel(t.Context())
	t.Cleanup(cancel)
	result := make(chan error, 1)
	go func() {
		result <- receiveLiveWithTimeouts(ctx, client, publisher, time.Second, time.Second)
	}()
	select {
	case <-publisher.pushed:
	case <-time.After(time.Second):
		t.Fatal("delayed, fragmented video packet was not published")
	}
	if err := <-serverDone; err != nil {
		t.Fatalf("server could not send fragmented LIVE packet: %v", err)
	}
	cancel()
	select {
	case err := <-result:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("receive loop returned %v after cancellation", err)
		}
	case <-time.After(time.Second):
		t.Fatal("receive loop did not stop after cancellation")
	}
}

func TestReceiveLiveHandlesCancellationRaceWithReadDeadline(t *testing.T) {
	client, _ := testPipe(t)
	conn := &readDeadlineRaceConn{
		Conn:    client,
		entered: make(chan struct{}),
		release: make(chan struct{}),
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() {
		result <- receiveLive(ctx, conn, &generationTestPublisher{})
	}()
	<-conn.entered
	cancel()
	close(conn.release)
	select {
	case err := <-result:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("expected cancellation after read-deadline race, got %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("receive loop did not stop after cancellation")
	}
}

func TestReceiveLiveDoesNotConvertParentDeadlineToSuccess(t *testing.T) {
	client, server := testPipe(t)
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	publisher := &generationTestPublisher{total: 1, current: 1, generation: 1}
	acknowledgment := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 2, "C": 0}})
	serverDone := make(chan error, 1)
	go func() {
		if err := writeFull(server, acknowledgment); err != nil {
			serverDone <- err
			return
		}
		serverDone <- writeFull(server, mediaPacket(1, []byte{1}))
	}()
	err := receiveLive(ctx, client, publisher)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected caller deadline error, got %v", err)
	}
	if err := <-serverDone; err != nil {
		t.Fatalf("server could not send LIVE packets: %v", err)
	}
}

func TestReceiveLiveReclassifiesPushErrorWhenContextEnds(t *testing.T) {
	tests := []struct {
		name        string
		cause       error
		wantErr     error
		wantSuccess bool
	}{
		{name: "caller cancellation", cause: context.Canceled, wantErr: context.Canceled},
		{name: "session duration", cause: errSessionDurationElapsed, wantSuccess: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			client, server := testPipe(t)
			ctx, cancel := context.WithCancelCause(context.Background())
			defer cancel(context.Canceled)
			publisher := &blockedPushPublisher{
				entered: make(chan struct{}),
				release: make(chan struct{}),
			}
			acknowledgment := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 2, "C": 0}})
			serverDone := make(chan error, 1)
			go func() {
				packets := [][]byte{
					acknowledgment,
					mediaPacket(1, []byte{1}),
					mediaPacket(2, []byte{2}),
				}
				for _, packet := range packets {
					if err := writeFull(server, packet); err != nil {
						serverDone <- err
						return
					}
				}
				serverDone <- nil
			}()
			result := make(chan error, 1)
			go func() {
				result <- receiveLive(ctx, client, publisher)
			}()
			select {
			case <-publisher.entered:
			case <-time.After(time.Second):
				t.Fatal("publisher did not enter the blocked write")
			}
			cancel(test.cause)
			close(publisher.release)
			select {
			case err := <-result:
				if test.wantSuccess && err != nil {
					t.Fatalf("expected clean session end, got %v", err)
				}
				if test.wantErr != nil && !errors.Is(err, test.wantErr) {
					t.Fatalf("expected %v, got %v", test.wantErr, err)
				}
			case <-time.After(time.Second):
				t.Fatal("receive loop did not stop after context ended")
			}
			if err := <-serverDone; err != nil {
				t.Fatalf("server could not send LIVE packets: %v", err)
			}
		})
	}
}

func TestReceiveLiveInterruptsWhenVideoStopsWhileTCPRemainsOpen(t *testing.T) {
	client, server := testPipe(t)
	ack := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 2, "C": 0}})
	serverDone := make(chan error, 1)
	go func() {
		if err := writeFull(server, ack); err != nil {
			serverDone <- err
			return
		}
		serverDone <- writeFull(server, mediaPacket(1, []byte{1}))
	}()
	err := receiveLiveWithTimeouts(t.Context(), client, &generationTestPublisher{}, time.Second, 100*time.Millisecond)
	if !errors.Is(err, ErrStreamInterrupted) {
		t.Fatalf("silent video on open TCP connection: %v", err)
	}
	if err := <-serverDone; err != nil {
		t.Fatal(err)
	}
}

func TestReceiveLiveRejectsInactivePublicationAtSessionEnd(t *testing.T) {
	client, server := testPipe(t)
	publisher := &generationTestPublisher{resetOnly: true}
	acknowledgment := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 2, "C": 0}})
	serverDone := make(chan error, 1)
	go func() {
		for _, packet := range [][]byte{
			acknowledgment,
			mediaPacket(1, []byte{1}),
			mediaPacket(2, []byte{2}),
		} {
			if err := writeFull(server, packet); err != nil {
				serverDone <- err
				return
			}
		}
		serverDone <- nil
	}()
	ctx, cancel := context.WithTimeoutCause(context.Background(), 100*time.Millisecond, errSessionDurationElapsed)
	defer cancel()
	err := receiveLive(ctx, client, publisher)
	if !errors.Is(err, ErrVideoStartup) {
		t.Fatalf("expected inactive-publication startup error, got %v", err)
	}
	if publisher.PublishedFrames() != 5 || publisher.CurrentPublicationFrames() != 0 {
		t.Fatalf("unexpected publication generations: total=%d current=%d",
			publisher.PublishedFrames(), publisher.CurrentPublicationFrames())
	}
	if err := <-serverDone; err != nil {
		t.Fatalf("server could not send LIVE packets: %v", err)
	}
}

func TestReceiveLiveTracksNewPublicationGeneration(t *testing.T) {
	client, server := testPipe(t)
	publisher := &generationTestPublisher{}
	acknowledgment := mustEncodeCommand(t, []map[string]any{{"A": 3, "V": 2, "C": 0}})
	serverDone := make(chan error, 1)
	go func() {
		for _, packet := range [][]byte{
			acknowledgment,
			mediaPacket(1, []byte{1}),
			mediaPacket(2, []byte{2}),
			mediaPacket(3, []byte{3}),
		} {
			if err := writeFull(server, packet); err != nil {
				serverDone <- err
				return
			}
		}
		serverDone <- nil
	}()
	ctx, cancel := context.WithTimeoutCause(context.Background(), 100*time.Millisecond, errSessionDurationElapsed)
	defer cancel()
	if err := receiveLive(ctx, client, publisher); err != nil {
		t.Fatalf("new publication generation was treated as silent: %v", err)
	}
	if publisher.Generation() != 2 || publisher.CurrentPublicationFrames() != 1 {
		t.Fatalf("unexpected publication generation: generation=%d current=%d",
			publisher.Generation(), publisher.CurrentPublicationFrames())
	}
	if err := <-serverDone; err != nil {
		t.Fatalf("server could not send LIVE packets: %v", err)
	}
}

type generationTestPublisher struct {
	total      uint64
	current    uint64
	generation uint64
	resetOnly  bool
	pushed     chan struct{}
}

func TestReceiveLiveRejectsRepeatedVideoTimestampAndPayload(t *testing.T) {
	publisher := &generationTestPublisher{}
	var observed []SessionEvent
	receiver := liveReceiver{
		ctx:            context.Background(),
		publisher:      publisher,
		startupTimeout: time.Second,
		silenceTimeout: time.Second,
		observe:        func(event SessionEvent) { observed = append(observed, event) },
	}
	frame := videoFrame{timestamp: 42, accessUnit: []byte{1}}
	if _, err := receiver.handleVideo(frame); err != nil {
		t.Fatal(err)
	}
	firstPublished := receiver.lastPublished
	if _, err := receiver.handleVideo(frame); err != nil {
		t.Fatal(err)
	}
	if receiver.lastPublished != firstPublished {
		t.Fatal("duplicate frame was treated as fresh video")
	}
	if len(observed) != 1 || observed[0].FrameAt.IsZero() {
		t.Fatalf("frame observations = %+v, want only the published frame", observed)
	}
	if _, err := receiver.handleVideo(videoFrame{timestamp: 42, accessUnit: []byte{2}}); err != nil {
		t.Fatalf("different access unit at the same timestamp: %v", err)
	}
	if publisher.Generation() != 2 {
		t.Fatal("different access unit at the same timestamp was discarded")
	}
	receiver.timestampAt = time.Now().Add(-time.Second)
	if _, err := receiver.handleVideo(frame); !errors.Is(err, ErrStreamInterrupted) {
		t.Fatalf("repeated timestamp error = %v, want stream interruption", err)
	}
	if _, err := receiver.handleVideo(videoFrame{timestamp: 41, accessUnit: []byte{1}}); err != nil {
		t.Fatalf("timestamp regression should be tolerated: %v", err)
	}
}

type audioTestPublisher struct {
	generationTestPublisher
	audioFrames     []audioFrame
	audioParameters []audioParameters
	audioErr        error
	resetVideo      bool
}

// Exercise the real audio publisher while keeping the video side synthetic.
type realAudioTestPublisher struct {
	generationTestPublisher
	audio       videoPublisher
	audioCalls  int
	audioError  error
	secondVideo chan struct{}
}

func (publisher *realAudioTestPublisher) Push(accessUnit []byte, timestamp uint32) error {
	err := publisher.generationTestPublisher.Push(accessUnit, timestamp)
	if accessUnit[0] == 2 {
		close(publisher.secondVideo)
	}
	return err
}

func (publisher *realAudioTestPublisher) PushAudio(frame audioFrame, parameters *audioParameters) error {
	publisher.audioCalls++
	publisher.audioError = publisher.audio.PushAudio(frame, parameters)
	return publisher.audioError
}

func (publisher *audioTestPublisher) PushAudio(frame audioFrame, parameters *audioParameters) error {
	publisher.audioFrames = append(publisher.audioFrames, frame)
	publisher.audioParameters = append(publisher.audioParameters, *parameters)
	if publisher.audioErr != nil {
		return publisher.audioErr
	}
	if publisher.resetVideo && publisher.CurrentPublicationFrames() > 0 {
		publisher.current = 0
	}
	return nil
}

func audioTestPacket(pcm bool, timestamp uint32, payload []byte) []byte {
	header := byte(audioFrameType << 4)
	if pcm {
		header |= audioPCMFlag
	}
	body := make([]byte, mediaHeaderSize+len(payload))
	body[0] = header
	binary.LittleEndian.PutUint32(body[1:mediaHeaderSize], timestamp)
	copy(body[mediaHeaderSize:], payload)
	return makePacketKind(uint32(len(body)), 0, packetMedia, body)
}

func testPipe(t *testing.T) (net.Conn, net.Conn) {
	t.Helper()
	client, server := net.Pipe()
	t.Cleanup(func() {
		_ = client.Close()
		_ = server.Close()
	})
	return client, server
}

type blockedPushPublisher struct {
	generationTestPublisher
	entered chan struct{}
	release chan struct{}
}

func (publisher *blockedPushPublisher) Push(accessUnit []byte, timestamp uint32) error {
	if accessUnit[0] == 2 {
		close(publisher.entered)
		<-publisher.release
		return net.ErrClosed
	}
	return publisher.generationTestPublisher.Push(accessUnit, timestamp)
}

type readDeadlineRaceConn struct {
	net.Conn
	entered chan struct{}
	release chan struct{}
}

func (conn *readDeadlineRaceConn) SetReadDeadline(time.Time) error {
	close(conn.entered)
	<-conn.release
	return net.ErrClosed
}

func (publisher *generationTestPublisher) Push(accessUnit []byte, _ uint32) error {
	switch accessUnit[0] {
	case 1:
		publisher.generation = 1
		publisher.total = 5
		publisher.current = 5
	case 2:
		if publisher.resetOnly {
			publisher.current = 0
		} else {
			publisher.generation++
			publisher.total++
			publisher.current = 1
		}
	case 3:
	}
	if publisher.pushed != nil {
		select {
		case <-publisher.pushed:
		default:
			close(publisher.pushed)
		}
	}
	return nil
}

func (publisher *generationTestPublisher) SetStartupDeadline(time.Time) {}

func (publisher *generationTestPublisher) PublishedFrames() uint64 {
	return publisher.total
}

func (publisher *generationTestPublisher) CurrentPublicationFrames() uint64 {
	return publisher.current
}

func (publisher *generationTestPublisher) Generation() uint64 {
	return publisher.generation
}

func mediaPacket(timestamp uint32, accessUnit []byte) []byte {
	body := make([]byte, 5+len(accessUnit))
	body[0] = 0x10
	binary.LittleEndian.PutUint32(body[1:5], timestamp)
	copy(body[5:], accessUnit)
	return makePacketKind(uint32(len(body)), 0, packetMedia, body)
}
