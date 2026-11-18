package manager

import (
	"strings"
	"testing"
)

func TestWebRTCOfferAudioDirections(t *testing.T) {
	tests := []struct {
		name        string
		direction   string
		microphone  bool
		cameraAudio bool
	}{
		{name: "send only", direction: "sendonly", microphone: true},
		{name: "receive only", direction: "recvonly", cameraAudio: true},
		{name: "bidirectional", direction: "sendrecv", microphone: true, cameraAudio: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			offer := testAudioSDP(test.direction)
			microphone, err := offeredMicrophone(offer)
			if err != nil || microphone != test.microphone {
				t.Fatalf("offeredMicrophone() = %t, %v", microphone, err)
			}
			cameraAudio, err := offeredCameraAudio(offer)
			if err != nil || cameraAudio != test.cameraAudio {
				t.Fatalf("offeredCameraAudio() = %t, %v", cameraAudio, err)
			}
		})
	}
	unsupported := strings.Replace(testAudioSDP("recvonly"), "a=rtpmap:111 opus/48000/2\r\n", "", 1)
	if _, err := offeredCameraAudio(unsupported); err == nil {
		t.Fatal("camera audio without Opus was accepted")
	}
}

func TestWebRTCAnswerAudioDirection(t *testing.T) {
	for _, test := range []struct {
		direction string
		want      bool
	}{
		{direction: "sendonly", want: true},
		{direction: "sendrecv", want: true},
		{direction: "recvonly", want: false},
		{direction: "inactive", want: false},
	} {
		got, err := answerCanSendAudio(testAudioSDP(test.direction))
		if err != nil || got != test.want {
			t.Fatalf("answerCanSendAudio(%s) = %t, %v", test.direction, got, err)
		}
	}
}

func testAudioSDP(direction string) string {
	return "v=0\r\n" +
		"o=- 0 0 IN IP4 127.0.0.1\r\n" +
		"s=-\r\n" +
		"t=0 0\r\n" +
		"m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n" +
		"c=IN IP4 0.0.0.0\r\n" +
		"a=rtpmap:111 opus/48000/2\r\n" +
		"a=" + direction + "\r\n"
}
