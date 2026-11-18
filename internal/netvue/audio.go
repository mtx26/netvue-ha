package netvue

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/gortsplib/v5/pkg/format/rtplpcm"
	"github.com/bluenviron/gortsplib/v5/pkg/format/rtpmpeg4audio"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/mpeg4audio"
	"github.com/pion/rtp"
)

const (
	audioFrameType = 2
	audioPCMFlag   = 0x02
	audioMaxSize   = 64 * 1024
)

var (
	ErrAudioFrame                 = errors.New("invalid Netvue audio frame")
	ErrUnsupportedAudioParameters = errors.New("unsupported Netvue audio parameters")
)

type audioFrame struct {
	timestamp uint32
	payload   []byte
	pcm       bool
}

type audioParameters struct {
	sampleRate int
	sampleSize int
	channels   int
}

type audioTrack struct {
	format     format.Format
	sampleRate int
	channels   int
	sampleSize int
	pcm        bool
}

type timedAudioPacket struct {
	packet       *rtp.Packet
	sampleOffset uint32
	sampleCount  uint32
}

func parseAudioFrame(body []byte) (audioFrame, bool, error) {
	if len(body) < mediaHeaderSize+1 {
		return audioFrame{}, false, withReason("truncated frame header or empty payload", fmt.Errorf("%w: truncated audio frame", ErrAudioFrame))
	}
	if body[0]>>4 != audioFrameType {
		return audioFrame{}, false, nil
	}
	return audioFrame{
		timestamp: binary.LittleEndian.Uint32(body[1:mediaHeaderSize]),
		payload:   body[mediaHeaderSize:],
		pcm:       body[0]&audioPCMFlag != 0,
	}, true, nil
}

func parseAudioParameters(unit map[string]json.RawMessage) (*audioParameters, error) {
	rawChannels, ok := unit["P"]
	if !ok {
		return nil, nil
	}
	var channels []json.RawMessage
	if err := json.Unmarshal(rawChannels, &channels); err != nil {
		return nil, withReason("invalid selected channel list", fmt.Errorf("%w: invalid selected channel list", ErrProtocol))
	}
	if channels == nil {
		return nil, withReason("selected channel list is not an array", fmt.Errorf("%w: selected channel list is not an array", ErrProtocol))
	}
	for _, rawChannel := range channels {
		var values []json.RawMessage
		if err := json.Unmarshal(rawChannel, &values); err != nil || len(values) != 4 {
			return nil, withReason("invalid selected channel", fmt.Errorf("%w: invalid selected channel", ErrProtocol))
		}
		var mediaType int
		if err := json.Unmarshal(values[2], &mediaType); err != nil {
			return nil, withReason("invalid selected channel type", fmt.Errorf("%w: invalid selected channel type", ErrProtocol))
		}
		if mediaType != audioFrameType {
			continue
		}
		var rawParameters []json.RawMessage
		if err := json.Unmarshal(values[3], &rawParameters); err != nil || len(rawParameters) != 3 {
			return nil, withReason("invalid audio channel parameters", fmt.Errorf("%w: invalid audio channel parameters", ErrProtocol))
		}
		var parameters [3]int
		for i, raw := range rawParameters {
			var value *int
			if err := json.Unmarshal(raw, &value); err != nil || value == nil {
				return nil, withReason("invalid audio channel parameters", fmt.Errorf("%w: invalid audio channel parameters", ErrProtocol))
			}
			parameters[i] = *value
		}
		result := &audioParameters{
			sampleRate: parameters[0],
			sampleSize: parameters[1],
			channels:   parameters[2],
		}
		// The LIVE sample-size field is not defined as a bit depth; AAC
		// derives its format from ADTS, independently of this field.
		if result.sampleRate < 8000 || result.sampleRate > 48000 ||
			result.channels < 1 || result.channels > 2 {
			err := fmt.Errorf("%w: %w", ErrProtocol, ErrUnsupportedAudioParameters)
			return nil, withReason("audio channel parameters out of range", err)
		}
		return result, nil
	}
	return nil, nil
}

func newAudioTrack(frame audioFrame, parameters *audioParameters) (*audioTrack, error) {
	if len(frame.payload) == 0 || len(frame.payload) > audioMaxSize {
		return nil, withReason("audio frame size out of range", fmt.Errorf("%w: audio frame size out of range", ErrAudioFrame))
	}
	if frame.pcm {
		if parameters == nil {
			return nil, withReason("missing PCM channel parameters", fmt.Errorf("%w: PCM requires audio channel parameters", ErrAudioFrame))
		}
		trackFormat := &format.LPCM{
			PayloadTyp:   96,
			BitDepth:     16,
			SampleRate:   parameters.sampleRate,
			ChannelCount: parameters.channels,
		}
		if _, err := trackFormat.CreateEncoder(); err != nil {
			return nil, fmt.Errorf("%w: create LPCM RTP encoder: %w", ErrAudioFrame, err)
		}
		return &audioTrack{
			format:     trackFormat,
			sampleRate: parameters.sampleRate,
			channels:   parameters.channels,
			sampleSize: 16,
			pcm:        true,
		}, nil
	}

	var packets mpeg4audio.ADTSPackets
	if err := packets.Unmarshal(frame.payload); err != nil || len(packets) == 0 {
		if err == nil {
			err = errors.New("AAC frame has no access units")
		}
		return nil, withReason("invalid AAC/ADTS frame", fmt.Errorf("%w: parse AAC/ADTS frame: %w", ErrAudioFrame, err))
	}
	first := packets[0]
	if first.SampleRate < 8000 || first.SampleRate > 48000 ||
		first.ChannelCount < 1 || first.ChannelCount > 2 {
		return nil, withReason("AAC sample format out of range", fmt.Errorf("%w: AAC sample format out of range", ErrAudioFrame))
	}
	if parameters != nil &&
		(first.SampleRate != parameters.sampleRate || first.ChannelCount != parameters.channels) {
		return nil, withReason("AAC format does not match selected channel", fmt.Errorf("%w: AAC format does not match selected channel", ErrAudioFrame))
	}
	for _, packet := range packets[1:] {
		if packet.Type != first.Type || packet.SampleRate != first.SampleRate ||
			packet.ChannelConfig != first.ChannelConfig {
			return nil, withReason("AAC format changes within frame", fmt.Errorf("%w: AAC format changes within frame", ErrAudioFrame))
		}
	}
	config := &mpeg4audio.AudioSpecificConfig{
		Type:          first.Type,
		SampleRate:    first.SampleRate,
		ChannelConfig: first.ChannelConfig,
	}
	trackFormat := &format.MPEG4Audio{
		PayloadTyp:       97,
		Config:           config,
		SizeLength:       13,
		IndexLength:      3,
		IndexDeltaLength: 3,
	}
	if _, err := trackFormat.CreateEncoder(); err != nil {
		return nil, fmt.Errorf("%w: create AAC RTP encoder: %w", ErrAudioFrame, err)
	}
	return &audioTrack{
		format:     trackFormat,
		sampleRate: first.SampleRate,
		channels:   first.ChannelCount,
		pcm:        false,
	}, nil
}

func (track *audioTrack) matches(other *audioTrack) bool {
	if track == nil || other == nil || track.pcm != other.pcm ||
		track.sampleRate != other.sampleRate || track.channels != other.channels {
		return false
	}
	if track.pcm {
		return track.sampleSize == other.sampleSize
	}
	current := track.format.(*format.MPEG4Audio)
	next := other.format.(*format.MPEG4Audio)
	return *current.Config == *next.Config
}

func (track *audioTrack) encode(
	frame audioFrame,
	aacEncoder *rtpmpeg4audio.Encoder,
	lpcmEncoder *rtplpcm.Encoder,
) ([]timedAudioPacket, error) {
	if track.pcm {
		if len(frame.payload)%(track.channels*track.sampleSize/8) != 0 {
			return nil, withReason("PCM frame is not sample-aligned", fmt.Errorf("%w: PCM frame is not sample-aligned", ErrAudioFrame))
		}
		samples := make([]byte, len(frame.payload))
		for i := 0; i < len(frame.payload); i += 2 {
			samples[i], samples[i+1] = frame.payload[i+1], frame.payload[i]
		}
		packets, err := lpcmEncoder.Encode(samples)
		if err != nil {
			return nil, fmt.Errorf("packetize LPCM frame: %w", err)
		}
		result := make([]timedAudioPacket, 0, len(packets))
		var offset uint32
		sampleSize := track.channels * track.sampleSize / 8
		for _, packet := range packets {
			samples := uint32(len(packet.Payload) / sampleSize)
			result = append(result, timedAudioPacket{packet: packet, sampleOffset: offset, sampleCount: samples})
			offset += samples
		}
		return result, nil
	}

	var packets mpeg4audio.ADTSPackets
	if err := packets.Unmarshal(frame.payload); err != nil || len(packets) == 0 {
		if err == nil {
			err = errors.New("AAC frame has no access units")
		}
		return nil, withReason("invalid AAC/ADTS frame", fmt.Errorf("%w: parse AAC/ADTS frame: %w", ErrAudioFrame, err))
	}
	result := make([]timedAudioPacket, 0, len(packets))
	for index, packet := range packets {
		rtpPackets, err := aacEncoder.Encode([][]byte{packet.AU})
		if err != nil {
			return nil, fmt.Errorf("packetize AAC access unit: %w", err)
		}
		for _, rtpPacket := range rtpPackets {
			result = append(result, timedAudioPacket{
				packet:       rtpPacket,
				sampleOffset: uint32(index * mpeg4audio.SamplesPerAccessUnit),
				sampleCount:  mpeg4audio.SamplesPerAccessUnit,
			})
		}
	}
	return result, nil
}
