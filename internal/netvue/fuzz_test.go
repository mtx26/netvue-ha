package netvue

import (
	"bytes"
	"testing"
	"time"
)

func FuzzParseAudioFrame(f *testing.F) {
	f.Add([]byte{0x20, 1, 0, 0, 0, 0xaa})
	f.Add([]byte{0x22, 1, 0, 0, 0, 0xaa, 0xbb})
	f.Add([]byte{0x10, 0, 0, 0, 0, 1})
	f.Add([]byte{0x20, 0, 0, 0, 0})
	f.Add([]byte{})
	f.Fuzz(func(t *testing.T, body []byte) {
		frame, audio, err := parseAudioFrame(body)
		if err != nil && audio {
			t.Fatalf("parseAudioFrame returned audio with error %v", err)
		}
		if err != nil {
			return
		}
		if audio && (len(frame.payload) == 0 || len(frame.payload) != len(body)-mediaHeaderSize) {
			t.Fatalf("audio payload length %d for body length %d", len(frame.payload), len(body))
		}
		if audio && frame.pcm != (body[0]&audioPCMFlag != 0) {
			t.Fatal("PCM flag does not match header")
		}
	})
}

func FuzzParseAudioParameters(f *testing.F) {
	for _, seed := range []string{
		`{"P":[[1,1,2,[8000,16,1]]]}`,
		`{"P":[[1,1,1,[0,0,0]],[1,1,2,[48000,16,2]]]}`,
		`{"P":[[1,1,2,[1,16,1]]]}`,
		`{"P":[[1,1,2,[8000,null,1]]]}`,
		`{"P":null}`,
		`{"P":"x"}`,
		`{}`,
	} {
		f.Add([]byte(seed))
	}
	f.Fuzz(func(t *testing.T, body []byte) {
		// The same JSON object model as ParseUnits: one command unit.
		units, err := ParseUnits(append(append([]byte{'['}, body...), ']'))
		if err != nil || len(units) != 1 {
			return
		}
		parameters, err := parseAudioParameters(units[0])
		if err != nil {
			if parameters != nil {
				t.Fatal("parseAudioParameters returned values with an error")
			}
			return
		}
		if parameters != nil && (parameters.sampleRate < 8000 || parameters.sampleRate > 48000 ||
			parameters.channels < 1 || parameters.channels > 2) {
			t.Fatalf("accepted out-of-range parameters %+v", *parameters)
		}
	})
}

func FuzzReadTalkAudio(f *testing.F) {
	frame := testADTS(11, 1, 42)
	f.Add(frame)
	f.Add(append(append([]byte{}, frame...), frame...))
	f.Add(testADTS(3, 2, 1))
	f.Add(frame[:6])
	f.Add([]byte{0xff, 0xf1})
	f.Add([]byte{})
	f.Fuzz(func(t *testing.T, data []byte) {
		audio, err := readTalkAudio(bytes.NewReader(data))
		if err != nil {
			return
		}
		if len(audio.frames) == 0 || audio.rate < 8000 || audio.rate > 48000 ||
			audio.channels < 1 || audio.channels > 2 || audio.duration() > maxTalkDuration {
			t.Fatalf("accepted invalid audio: frames=%d rate=%d channels=%d", len(audio.frames), audio.rate, audio.channels)
		}
		// Accepted frames must exactly tile the input: no dropped or invented bytes.
		if !bytes.Equal(bytes.Join(audio.frames, nil), data) {
			t.Fatal("ADTS frames do not reproduce the input")
		}
		if audio.duration() <= time.Duration(0) {
			t.Fatal("non-positive audio duration")
		}
	})
}
