package netvue

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"testing"

	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/mpeg4audio"
)

func TestParseAudioFrame(t *testing.T) {
	tests := []struct {
		name      string
		header    byte
		wantAudio bool
		wantPCM   bool
	}{
		{name: "AAC", header: 0x20, wantAudio: true},
		{name: "PCM", header: 0x22, wantAudio: true, wantPCM: true},
		{name: "video", header: 0x10},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			body := []byte{test.header, 0, 0, 0, 0, 1, 2}
			binary.LittleEndian.PutUint32(body[1:5], 0x12345678)
			frame, isAudio, err := parseAudioFrame(body)
			if err != nil {
				t.Fatal(err)
			}
			if isAudio != test.wantAudio {
				t.Fatalf("isAudio = %t, want %t", isAudio, test.wantAudio)
			}
			if isAudio && (frame.timestamp != 0x12345678 || frame.pcm != test.wantPCM ||
				!bytes.Equal(frame.payload, []byte{1, 2})) {
				t.Fatalf("unexpected frame: %+v", frame)
			}
		})
	}
	if _, _, err := parseAudioFrame([]byte{0x20, 0, 0, 0, 0}); !errors.Is(err, ErrAudioFrame) {
		t.Fatalf("truncated audio frame error = %v, want ErrAudioFrame", err)
	}
}

func TestParseAudioParameters(t *testing.T) {
	for _, test := range []struct {
		name       string
		raw        string
		want       *audioParameters
		wantErrors []error
	}{
		{
			name: "audio parameters after a non-audio channel",
			raw:  `[[0,1,1,[1,0]], [1,1,2,[16000,16,1]]]`,
			want: &audioParameters{sampleRate: 16000, sampleSize: 16, channels: 1},
		},
		{
			name: "observed mono LIVE parameters",
			raw:  `[[1,1,2,[8000,1,1]]]`,
			want: &audioParameters{sampleRate: 8000, sampleSize: 1, channels: 1},
		},
		{name: "null channel list", raw: `null`, wantErrors: []error{ErrProtocol}},
		{name: "missing channel parameter", raw: `[[1,1,2,[16000,16]]]`, wantErrors: []error{ErrProtocol}},
		{name: "null sample size", raw: `[[1,1,2,[8000,null,1]]]`, wantErrors: []error{ErrProtocol}},
		{name: "unsupported channel count", raw: `[[1,1,2,[16000,16,3]]]`, wantErrors: []error{ErrProtocol}},
		{
			name: "unsupported sample rate",
			raw:  `[[1,1,2,[96000,16,1]]]`,
			wantErrors: []error{
				ErrProtocol,
				ErrUnsupportedAudioParameters,
			},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			parameters, err := parseAudioParameters(map[string]json.RawMessage{
				"P": json.RawMessage(test.raw),
			})
			if test.want != nil {
				if err != nil || parameters == nil || *parameters != *test.want {
					t.Fatalf("parameters=%+v error=%v, want %+v", parameters, err, *test.want)
				}
				return
			}
			for _, want := range test.wantErrors {
				if !errors.Is(err, want) {
					t.Fatalf("parseAudioParameters() error = %v, want errors.Is(_, %v)", err, want)
				}
			}
		})
	}
}

func TestNewAACTrackParsesADTSAndCreatesRTPPackets(t *testing.T) {
	adts, err := (mpeg4audio.ADTSPackets{{
		Type:          mpeg4audio.ObjectTypeAACLC,
		SampleRate:    8000,
		ChannelConfig: 1,
		AU:            []byte{1, 2, 3},
	}}).Marshal()
	if err != nil {
		t.Fatal(err)
	}
	frame := audioFrame{payload: adts}
	parameters := &audioParameters{sampleRate: 8000, sampleSize: 1, channels: 1}
	track, err := newAudioTrack(frame, parameters)
	if err != nil {
		t.Fatal(err)
	}
	audioFormat, ok := track.format.(*format.MPEG4Audio)
	if !ok || audioFormat.Config.SampleRate != 8000 || audioFormat.Config.ChannelConfig != 1 {
		t.Fatalf("unexpected AAC format: %#v", track.format)
	}
	encoder, err := audioFormat.CreateEncoder()
	if err != nil {
		t.Fatal(err)
	}
	packets, err := track.encode(frame, encoder, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(packets) != 1 || packets[0].packet.PayloadType != 97 {
		t.Fatalf("unexpected AAC RTP packets: %+v", packets)
	}
}

func TestAudioRTPClockDoesNotOverlapMultiAUPayload(t *testing.T) {
	au := &mpeg4audio.ADTSPacket{
		Type:          mpeg4audio.ObjectTypeAACLC,
		SampleRate:    8000,
		ChannelConfig: 1,
		AU:            []byte{1, 2, 3},
	}
	multi, err := (mpeg4audio.ADTSPackets{au, au}).Marshal()
	if err != nil {
		t.Fatal(err)
	}
	single, err := (mpeg4audio.ADTSPackets{au}).Marshal()
	if err != nil {
		t.Fatal(err)
	}
	track, err := newAudioTrack(audioFrame{payload: multi}, nil)
	if err != nil {
		t.Fatal(err)
	}
	encoder, err := track.format.(*format.MPEG4Audio).CreateEncoder()
	if err != nil {
		t.Fatal(err)
	}
	publisher := &videoPublisher{audioTrack: track, videoRTPStart: 90_000}
	publisher.clock.update(1000)
	first, err := track.encode(audioFrame{payload: multi}, encoder, nil)
	if err != nil || len(first) != 2 {
		t.Fatalf("two-AU packetization: packets=%d err=%v", len(first), err)
	}
	publisher.timestampAudioPackets(1000, first)
	second, err := track.encode(audioFrame{payload: single}, encoder, nil)
	if err != nil || len(second) != 1 {
		t.Fatalf("one-AU packetization: packets=%d err=%v", len(second), err)
	}
	publisher.timestampAudioPackets(1064, second)
	if delta := int32(second[0].packet.Timestamp -
		first[len(first)-1].packet.Timestamp); delta != 1024 {
		t.Fatalf("next AAC timestamp advances %d samples after previous AU, want 1024", delta)
	}
}

func TestAudioRTPClockHandlesFragmentsWrapAndSourceGap(t *testing.T) {
	au := &mpeg4audio.ADTSPacket{
		Type:          mpeg4audio.ObjectTypeAACLC,
		SampleRate:    8000,
		ChannelConfig: 1,
		AU:            make([]byte, 3000),
	}
	data, err := (mpeg4audio.ADTSPackets{au, au}).Marshal()
	if err != nil {
		t.Fatal(err)
	}
	frame := audioFrame{payload: data}
	track, err := newAudioTrack(frame, nil)
	if err != nil {
		t.Fatal(err)
	}
	encoder, err := track.format.(*format.MPEG4Audio).CreateEncoder()
	if err != nil {
		t.Fatal(err)
	}
	packets, err := track.encode(frame, encoder, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(packets) <= 2 {
		t.Fatalf("expected fragmented AUs, got %d packets", len(packets))
	}
	publisher := &videoPublisher{audioTrack: track}
	publisher.clock.update(1000)
	publisher.clock.elapsedMS = 536_870_900
	publisher.timestampAudioPackets(1000, packets)
	firstTimestamp := packets[0].packet.Timestamp
	for _, packet := range packets {
		if got := packet.packet.Timestamp - firstTimestamp; got != packet.sampleOffset {
			t.Fatalf("fragment RTP offset = %d, want %d", got, packet.sampleOffset)
		}
	}
	if packets[len(packets)-1].packet.Timestamp >= firstTimestamp {
		t.Fatal("test did not cross the RTP uint32 timestamp wrap")
	}
	next, err := track.encode(frame, encoder, nil)
	if err != nil {
		t.Fatal(err)
	}
	publisher.timestampAudioPackets(1064, next)
	if got := next[0].packet.Timestamp - firstTimestamp; got != 2048 {
		t.Fatalf("next frame RTP offset across wrap = %d, want 2048", got)
	}
	later, err := track.encode(frame, encoder, nil)
	if err != nil {
		t.Fatal(err)
	}
	publisher.timestampAudioPackets(2000, later)
	if got := later[0].packet.Timestamp - firstTimestamp; got != 8000 {
		t.Fatalf("source gap was not preserved: offset=%d, want 8000", got)
	}
}

func TestAudioRTPClockAvoidsOverlapBetweenFragmentedPCMFrames(t *testing.T) {
	frame := audioFrame{pcm: true, payload: make([]byte, 4000)}
	track, err := newAudioTrack(frame, &audioParameters{sampleRate: 8000, channels: 1})
	if err != nil {
		t.Fatal(err)
	}
	encoder, err := track.format.(*format.LPCM).CreateEncoder()
	if err != nil {
		t.Fatal(err)
	}
	first, err := track.encode(frame, nil, encoder)
	if err != nil || len(first) < 2 {
		t.Fatalf("fragmented PCM: packets=%d err=%v", len(first), err)
	}
	publisher := &videoPublisher{audioTrack: track}
	publisher.clock.update(1000)
	publisher.timestampAudioPackets(1000, first)
	second, err := track.encode(frame, nil, encoder)
	if err != nil {
		t.Fatal(err)
	}
	publisher.timestampAudioPackets(1010, second)
	if got := second[0].packet.Timestamp - first[0].packet.Timestamp; got != 2000 {
		t.Fatalf("next PCM timestamp advances %d samples, want 2000", got)
	}
}

func TestNewPCMTrackPacketizesNetworkByteOrderL16(t *testing.T) {
	frame := audioFrame{payload: []byte{0x34, 0x12}, pcm: true}
	parameters := &audioParameters{sampleRate: 16000, sampleSize: 1, channels: 1}
	track, err := newAudioTrack(frame, parameters)
	if err != nil {
		t.Fatal(err)
	}
	pcmFormat, ok := track.format.(*format.LPCM)
	if !ok {
		t.Fatalf("audio format = %T, want LPCM", track.format)
	}
	encoder, err := pcmFormat.CreateEncoder()
	if err != nil {
		t.Fatal(err)
	}
	packets, err := track.encode(frame, nil, encoder)
	if err != nil {
		t.Fatal(err)
	}
	if len(packets) != 1 || !bytes.Equal(packets[0].packet.Payload, []byte{0x12, 0x34}) {
		t.Fatalf("unexpected LPCM RTP packets: %+v", packets)
	}
}

func TestNewAudioTrackRejectsUnsupportedPCMAndMalformedAAC(t *testing.T) {
	if _, err := newAudioTrack(
		audioFrame{payload: []byte{1}, pcm: true},
		nil,
	); !errors.Is(err, ErrAudioFrame) || errors.Is(err, ErrProtocol) {
		t.Fatalf("unsupported PCM error = %v, want ErrAudioFrame without ErrProtocol", err)
	}
	if _, err := newAudioTrack(audioFrame{payload: []byte{1}}, nil); !errors.Is(err, ErrAudioFrame) {
		t.Fatalf("malformed AAC error = %v, want ErrAudioFrame", err)
	}
}

func TestPushAudioClassifiesMalformedFramesAsAudioOnly(t *testing.T) {
	tests := []struct {
		name       string
		frame      audioFrame
		parameters *audioParameters
	}{
		{name: "empty payload", frame: audioFrame{}},
		{name: "oversized payload", frame: audioFrame{payload: make([]byte, audioMaxSize+1)}},
		{name: "PCM without selected parameters", frame: audioFrame{payload: []byte{1, 2}, pcm: true}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			publisher := &videoPublisher{}
			err := publisher.PushAudio(test.frame, test.parameters)
			if !errors.Is(err, ErrAudioFrame) || errors.Is(err, ErrProtocol) {
				t.Fatalf("PushAudio() error = %v, want ErrAudioFrame without ErrProtocol", err)
			}
		})
	}
}

func TestAudioRTPClockUsesTheVideoPresentationOrigin(t *testing.T) {
	publisher := &videoPublisher{
		audioTrack:    &audioTrack{sampleRate: 16000},
		videoRTPStart: 90_000,
	}
	publisher.clock.update(1_000)
	publisher.videoElapsedMSStart = publisher.clock.elapsedMS
	if got := publisher.audioRTPTimestamp(1_500, 16); got != 24_000 {
		t.Fatalf("audio RTP timestamp = %d, want 24000 for the shared 500 ms offset", got)
	}
	if got := publisher.audioRTPTimestamp(1_200, 16); got != 24_016 {
		t.Fatalf("audio RTP timestamp after a source regression = %d, want 24016", got)
	}

	wrappingPublisher := &videoPublisher{
		audioTrack:    &audioTrack{sampleRate: 16000},
		videoRTPStart: 90_000,
	}
	wrappingPublisher.clock.update(0xfffffff0)
	wrappingPublisher.videoElapsedMSStart = wrappingPublisher.clock.elapsedMS
	if got := wrappingPublisher.audioRTPTimestamp(0x10, 16); got != 16_512 {
		t.Fatalf("audio RTP timestamp across millisecond wrap = %d, want 16512", got)
	}
}

func TestAudioRTPClockFollowsVideoTimestampCorrections(t *testing.T) {
	tests := []struct {
		name        string
		videoStart  uint32
		videoNext   uint32
		wantElapsed uint32
	}{
		{name: "duplicate", videoStart: 1_000, videoNext: 1_000, wantElapsed: 1},
		{name: "regression", videoStart: 1_000, videoNext: 500, wantElapsed: 1},
		{name: "large gap", videoStart: 1_000, videoNext: 12_001, wantElapsed: 1},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			publisher := &videoPublisher{
				audioTrack:    &audioTrack{sampleRate: 16000},
				videoRTPStart: 90_000,
			}
			publisher.clock.update(test.videoStart)
			publisher.videoElapsedMSStart = publisher.clock.elapsedMS
			videoElapsed := publisher.clock.update(test.videoNext)
			audioTimestamp := publisher.audioRTPTimestamp(test.videoNext, 16)
			want := uint32(16_000 + test.wantElapsed*16)
			if got := uint32(16_000 + videoElapsed*16/90); got != want {
				t.Fatalf("normalized video RTP timestamp = %d, want %d", got, want)
			}
			if audioTimestamp != want {
				t.Fatalf("audio RTP timestamp = %d, want %d", audioTimestamp, want)
			}
		})
	}
}

func TestAudioRTPClocksAreIsolatedPerPublisher(t *testing.T) {
	first := &videoPublisher{
		audioTrack:    &audioTrack{sampleRate: 8000},
		videoRTPStart: 90_000,
	}
	second := &videoPublisher{
		audioTrack:    &audioTrack{sampleRate: 8000},
		videoRTPStart: 180_000,
	}
	first.clock.update(1_000)
	first.videoElapsedMSStart = first.clock.elapsedMS
	second.clock.update(5_000)
	second.videoElapsedMSStart = second.clock.elapsedMS

	first.clock.update(1_500)
	second.clock.update(5_250)
	if got := first.audioRTPTimestamp(1_500, 8); got != 12_000 {
		t.Fatalf("first camera audio RTP timestamp = %d, want 12000", got)
	}
	if got := second.audioRTPTimestamp(5_250, 8); got != 18_000 {
		t.Fatalf("second camera audio RTP timestamp = %d, want 18000", got)
	}
}

func TestAudioRTPClockRemainsContinuousBeyondSignedTimestampRange(t *testing.T) {
	publisher := &videoPublisher{
		audioTrack:    &audioTrack{sampleRate: 8000},
		videoRTPStart: 90_000,
	}
	sourceTimestamp := uint32(1000)
	publisher.clock.update(sourceTimestamp)
	publisher.videoElapsedMSStart = publisher.clock.elapsedMS

	elapsedMS := int64(1<<31) + 10_000
	for remaining := elapsedMS; remaining > 0; {
		step := uint32(remaining)
		if step > maxTimestampGap {
			step = maxTimestampGap
		}
		sourceTimestamp += step
		publisher.clock.update(sourceTimestamp)
		remaining -= int64(step)
	}

	got := publisher.audioRTPTimestamp(sourceTimestamp, 8)
	want := uint32(uint64(8000) + uint64(elapsedMS)*8)
	if got != want {
		t.Fatalf("audio RTP timestamp after %d ms = %d, want %d", elapsedMS, got, want)
	}
}
