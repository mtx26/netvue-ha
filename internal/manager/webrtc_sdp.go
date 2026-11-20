package manager

import (
	"encoding/hex"
	"errors"
	"strings"

	"github.com/pion/sdp/v3"
)

var errWebRTCCodecMismatch = errors.New("SDP lacks compatible H.264 video or Opus microphone")

func selectWebRTCVideo(offer string, sps []byte, microphone bool) ([]byte, bool, error) {
	if err := validateWebRTCSDP(offer, sps, true, microphone); err == nil {
		return sps, false, nil
	}
	if err := validateWebRTCSDP(offer, webRTCBaselineSPS, true, microphone); err != nil {
		return nil, false, err
	}
	return webRTCBaselineSPS, true, nil
}

// Require an exact H.264 profile/constraints match and a sufficient decoder
// level. Pion otherwise permits a MIME-only partial match when binding RTP.
func validateWebRTCSDP(text string, sps []byte, offer, requireMic bool) error {
	var session sdp.SessionDescription
	if err := session.UnmarshalString(text); err != nil {
		return err
	}
	videoOK, audioOK := false, false
	for _, m := range session.MediaDescriptions {
		if m.MediaName.Port.Value == 0 {
			continue
		}
		direction := effectiveWebRTCDirection(&session, m)
		if m.MediaName.Media == "video" &&
			((offer && (direction == "sendrecv" || direction == "recvonly")) ||
				(!offer && (direction == "sendrecv" || direction == "sendonly"))) {
			for _, payload := range m.MediaName.Formats {
				if !mediaHasRTPMap(m, payload, "h264/90000") {
					continue
				}
				params := mediaFMTP(m, payload)
				profile, err := hex.DecodeString(params["profile-level-id"])
				if err == nil && len(profile) == 3 && len(sps) >= 4 &&
					params["packetization-mode"] == "1" &&
					profile[0] == sps[1] && profile[1] == sps[2] && profile[2] >= sps[3] {
					videoOK = true
				}
			}
		}
		if m.MediaName.Media == "audio" &&
			((offer && (direction == "sendrecv" || direction == "sendonly")) ||
				(!offer && (direction == "sendrecv" || direction == "recvonly"))) {
			for _, payload := range m.MediaName.Formats {
				if mediaHasRTPMap(m, payload, "opus/48000/2") {
					audioOK = true
				}
			}
		}
	}
	if !videoOK || (requireMic && !audioOK) {
		return errWebRTCCodecMismatch
	}
	return nil
}

// A receive-only or absent audio m-line does not request microphone access.
func offeredMicrophone(text string) (bool, error) {
	var session sdp.SessionDescription
	if err := session.UnmarshalString(text); err != nil {
		return false, err
	}
	requested := false
	for _, m := range session.MediaDescriptions {
		if m.MediaName.Media != "audio" || m.MediaName.Port.Value == 0 {
			continue
		}
		direction := effectiveWebRTCDirection(&session, m)
		if direction != "sendrecv" && direction != "sendonly" {
			continue
		}
		requested = true
		for _, payload := range m.MediaName.Formats {
			if mediaHasRTPMap(m, payload, "opus/48000/2") {
				return true, nil
			}
		}
	}
	if requested {
		return false, errors.New("offered microphone does not support Opus")
	}
	return false, nil
}

func offeredCameraAudio(text string) (bool, error) {
	var session sdp.SessionDescription
	if err := session.UnmarshalString(text); err != nil {
		return false, err
	}
	for _, m := range session.MediaDescriptions {
		if m.MediaName.Media != "audio" || m.MediaName.Port.Value == 0 {
			continue
		}
		direction := effectiveWebRTCDirection(&session, m)
		if direction != "sendrecv" && direction != "recvonly" {
			continue
		}
		for _, payload := range m.MediaName.Formats {
			if mediaHasRTPMap(m, payload, "opus/48000/2") {
				return true, nil
			}
		}
		return false, errors.New("camera audio offer does not support Opus")
	}
	return false, nil
}

func answerCanSendAudio(text string) (bool, error) {
	var session sdp.SessionDescription
	if err := session.UnmarshalString(text); err != nil {
		return false, err
	}
	for _, m := range session.MediaDescriptions {
		if m.MediaName.Media != "audio" || m.MediaName.Port.Value == 0 {
			continue
		}
		direction := effectiveWebRTCDirection(&session, m)
		if direction != "sendrecv" && direction != "sendonly" {
			continue
		}
		for _, payload := range m.MediaName.Formats {
			if mediaHasRTPMap(m, payload, "opus/48000/2") {
				return true, nil
			}
		}
	}
	return false, nil
}

func effectiveWebRTCDirection(session *sdp.SessionDescription, media *sdp.MediaDescription) string {
	if direction := webRTCDirection(media.Attributes); direction != "" {
		return direction
	}
	if direction := webRTCDirection(session.Attributes); direction != "" {
		return direction
	}
	return "sendrecv"
}

func webRTCDirection(attributes []sdp.Attribute) string {
	for _, attribute := range attributes {
		switch attribute.Key {
		case "sendrecv", "sendonly", "recvonly", "inactive":
			return attribute.Key
		}
	}
	return ""
}

func mediaHasRTPMap(m *sdp.MediaDescription, payload, codec string) bool {
	for _, a := range m.Attributes {
		if a.Key == "rtpmap" {
			fields := strings.Fields(a.Value)
			if len(fields) == 2 && fields[0] == payload && strings.EqualFold(fields[1], codec) {
				return true
			}
		}
	}
	return false
}

func mediaFMTP(m *sdp.MediaDescription, payload string) map[string]string {
	params := make(map[string]string)
	for _, a := range m.Attributes {
		if a.Key != "fmtp" {
			continue
		}
		fields := strings.SplitN(a.Value, " ", 2)
		if len(fields) != 2 || fields[0] != payload {
			continue
		}
		for _, field := range strings.Split(fields[1], ";") {
			parts := strings.SplitN(strings.TrimSpace(field), "=", 2)
			if len(parts) == 2 {
				params[strings.ToLower(parts[0])] = strings.ToLower(parts[1])
			}
		}
	}
	return params
}
