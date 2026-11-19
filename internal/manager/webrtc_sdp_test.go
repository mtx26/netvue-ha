package manager

import (
	"bytes"
	"strings"
	"testing"
)

func TestWebRTCSelectVideoForChromiumOffer(t *testing.T) {
	offer := testAudioSDP("sendrecv") +
		"m=video 9 UDP/TLS/RTP/SAVPF 109 117\r\n" +
		"a=recvonly\r\n" +
		"a=rtpmap:109 H264/90000\r\n" +
		"a=fmtp:109 packetization-mode=1;profile-level-id=42e01f\r\n" +
		"a=rtpmap:117 H264/90000\r\n" +
		"a=fmtp:117 packetization-mode=1;profile-level-id=4d001f\r\n"
	cameraSPS := []byte{0x67, 0x4d, 0x00, 0x29}
	got, transcode, err := selectWebRTCVideo(offer, cameraSPS, true)
	if err != nil || !transcode || !bytes.Equal(got, []byte{0x67, 0x42, 0xe0, 0x1f}) {
		t.Fatalf("main 4.1 -> Chromium: profile %x, transcode=%t, err=%v", got, transcode, err)
	}
	got, transcode, err = selectWebRTCVideo(strings.Replace(offer, "4d001f", "4d0029", 1), cameraSPS, true)
	if err != nil || transcode || !bytes.Equal(got, cameraSPS) {
		t.Fatalf("main 4.1 -> main 4.1: profile %x, transcode=%t, err=%v", got, transcode, err)
	}
	for _, invalid := range []string{
		strings.ReplaceAll(offer, "packetization-mode=1", "packetization-mode=0"),
		strings.Replace(offer, "opus/48000/2", "PCMU/8000", 1),
	} {
		if _, _, err := selectWebRTCVideo(invalid, cameraSPS, true); err == nil {
			t.Fatal("accepted an offer without compatible H.264 or microphone codec")
		}
	}
}

func TestWebRTCOfferAudioDirections(t *testing.T) {
	tests := []struct {
		name        string
		offer       string
		microphone  bool
		cameraAudio bool
	}{
		{name: "send only", offer: testAudioSDP("sendonly"), microphone: true},
		{name: "receive only", offer: testAudioSDP("recvonly"), cameraAudio: true},
		{name: "bidirectional", offer: testAudioSDP("sendrecv"), microphone: true, cameraAudio: true},
		{name: "inactive", offer: testAudioSDP("inactive")},
		{name: "rejected media", offer: strings.Replace(testAudioSDP("sendrecv"), "m=audio 9", "m=audio 0", 1)},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			microphone, err := offeredMicrophone(test.offer)
			if err != nil || microphone != test.microphone {
				t.Fatalf("offeredMicrophone() = %t, %v", microphone, err)
			}
			cameraAudio, err := offeredCameraAudio(test.offer)
			if err != nil || cameraAudio != test.cameraAudio {
				t.Fatalf("offeredCameraAudio() = %t, %v", cameraAudio, err)
			}
		})
	}
	for _, test := range []struct {
		name  string
		sdp   string
		check func(string) (bool, error)
	}{
		{
			name:  "unsupported microphone",
			sdp:   strings.Replace(testAudioSDP("sendonly"), "a=rtpmap:111 opus/48000/2\r\n", "", 1),
			check: offeredMicrophone,
		},
		{
			name:  "unsupported camera audio",
			sdp:   strings.Replace(testAudioSDP("recvonly"), "a=rtpmap:111 opus/48000/2\r\n", "", 1),
			check: offeredCameraAudio,
		},
		{
			name:  "unmapped payload",
			sdp:   strings.Replace(testAudioSDP("recvonly"), "m=audio 9 UDP/TLS/RTP/SAVPF 111", "m=audio 9 UDP/TLS/RTP/SAVPF 112", 1),
			check: offeredCameraAudio,
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			if _, err := test.check(test.sdp); err == nil {
				t.Fatal("unsupported audio offer was accepted")
			}
		})
	}
}

func TestWebRTCAnswerAudioDirection(t *testing.T) {
	for _, test := range []struct {
		name      string
		direction string
		port      string
		supported bool
		want      bool
	}{
		{name: "send only", direction: "sendonly", port: "9", supported: true, want: true},
		{name: "bidirectional", direction: "sendrecv", port: "9", supported: true, want: true},
		{name: "receive only", direction: "recvonly", port: "9", supported: true},
		{name: "inactive", direction: "inactive", port: "9", supported: true},
		{name: "rejected media", direction: "sendrecv", port: "0", supported: true},
		{name: "unsupported codec", direction: "sendrecv", port: "9"},
	} {
		t.Run(test.name, func(t *testing.T) {
			sdp := testAudioSDP(test.direction)
			sdp = strings.Replace(sdp, "m=audio 9", "m=audio "+test.port, 1)
			if !test.supported {
				sdp = strings.Replace(sdp, "a=rtpmap:111 opus/48000/2\r\n", "", 1)
			}
			got, err := answerCanSendAudio(sdp)
			if err != nil || got != test.want {
				t.Fatalf("answerCanSendAudio() = %t, %v; want %t", got, err, test.want)
			}
		})
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
