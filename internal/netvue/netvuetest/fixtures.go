package netvuetest

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os/exec"
	"time"
)

// Frame is one media access unit emitted by the simulated camera.
type Frame struct {
	Data []byte
	Key  bool
}

// SolidH264 returns count identical IDR access units of one solid colour.
// Every unit carries SPS, PPS and an I_PCM IDR slice, so it decodes on its own
// and needs no encoder. Width and height must be multiples of 16. The stream is
// baseline profile 4:2:0, profile-level-id 42e01f.
func SolidH264(width, height int, r, g, b uint8, count int) []Frame {
	if width <= 0 || height <= 0 || width%16 != 0 || height%16 != 0 || count <= 0 {
		panic("netvuetest: invalid H.264 fixture geometry")
	}
	// BT.601 limited range, the matrix FFmpeg assumes for untagged video.
	fr, fg, fb := float64(r), float64(g), float64(b)
	clamp := func(v float64) byte {
		if v < 1 {
			return 1 // PCM samples must be non-zero.
		}
		if v > 254 {
			return 254
		}
		return byte(v + 0.5)
	}
	y := clamp(16 + 0.257*fr + 0.504*fg + 0.098*fb)
	u := clamp(128 - 0.148*fr - 0.291*fg + 0.439*fb)
	v := clamp(128 + 0.439*fr - 0.368*fg - 0.071*fb)

	sps := nal(0x67, spsRBSP(width/16, height/16))
	pps := nal(0x68, ppsRBSP())
	frames := make([]Frame, count)
	for i := range frames {
		var unit bytes.Buffer
		unit.Write(sps)
		unit.Write(pps)
		unit.Write(nal(0x65, idrRBSP(width/16*(height/16), i&1, y, u, v)))
		frames[i] = Frame{Data: unit.Bytes(), Key: true}
	}
	return frames
}

type bitWriter struct {
	buf  []byte
	bits uint
}

func (w *bitWriter) bit(v uint) {
	if w.bits%8 == 0 {
		w.buf = append(w.buf, 0)
	}
	if v != 0 {
		w.buf[len(w.buf)-1] |= 0x80 >> (w.bits % 8)
	}
	w.bits++
}

func (w *bitWriter) u(n int, v uint) {
	for i := n - 1; i >= 0; i-- {
		w.bit((v >> uint(i)) & 1)
	}
}

func (w *bitWriter) ue(v uint) {
	v++
	n := 0
	for t := v; t > 1; t >>= 1 {
		n++
	}
	w.u(n, 0)
	w.u(n+1, v)
}

func (w *bitWriter) se(v int) {
	if v > 0 {
		w.ue(uint(2*v - 1))
	} else {
		w.ue(uint(-2 * v))
	}
}

func (w *bitWriter) align() {
	for w.bits%8 != 0 {
		w.bit(0)
	}
}

func (w *bitWriter) trailing() {
	w.bit(1)
	w.align()
}

func spsRBSP(mbWidth, mbHeight int) []byte {
	w := &bitWriter{}
	w.u(8, 66)   // baseline
	w.u(8, 0xe0) // constraint_set0..2
	w.u(8, 31)   // level 3.1
	w.ue(0)      // sps id
	w.ue(0)      // log2_max_frame_num_minus4
	w.ue(2)      // pic_order_cnt_type: output order equals decoding order
	w.ue(1)      // max_num_ref_frames
	w.bit(0)     // gaps_in_frame_num_value_allowed
	w.ue(uint(mbWidth - 1))
	w.ue(uint(mbHeight - 1))
	w.bit(1) // frame_mbs_only
	w.bit(1) // direct_8x8_inference
	w.bit(0) // no cropping
	w.bit(0) // no VUI
	w.trailing()
	return w.buf
}

func ppsRBSP() []byte {
	w := &bitWriter{}
	w.ue(0) // pps id
	w.ue(0) // sps id
	w.bit(0)
	w.bit(0)
	w.ue(0)
	w.ue(0)
	w.ue(0)
	w.bit(0)
	w.u(2, 0)
	w.se(0)
	w.se(0)
	w.se(0)
	w.bit(0)
	w.bit(0)
	w.bit(0)
	w.trailing()
	return w.buf
}

func idrRBSP(macroblocks, idrID int, y, u, v byte) []byte {
	w := &bitWriter{}
	w.ue(0) // first_mb_in_slice
	w.ue(7) // I slice, all slices of the picture
	w.ue(0) // pps id
	w.u(4, 0)
	w.ue(uint(idrID))
	w.bit(0) // no_output_of_prior_pics
	w.bit(0) // long_term_reference
	w.se(0)  // slice_qp_delta
	for range macroblocks {
		w.ue(25) // I_PCM
		w.align()
		w.buf = append(w.buf, bytes.Repeat([]byte{y}, 256)...)
		w.buf = append(w.buf, bytes.Repeat([]byte{u}, 64)...)
		w.buf = append(w.buf, bytes.Repeat([]byte{v}, 64)...)
		w.bits += 384 * 8
	}
	w.trailing()
	return w.buf
}

// nal adds the Annex B start code and emulation-prevention bytes.
func nal(header byte, rbsp []byte) []byte {
	out := []byte{0, 0, 0, 1, header}
	zeros := 0
	for _, b := range rbsp {
		if zeros >= 2 && b <= 3 {
			out = append(out, 3)
			zeros = 0
		}
		out = append(out, b)
		if b == 0 {
			zeros++
		} else {
			zeros = 0
		}
	}
	return out
}

// SplitADTS splits concatenated ADTS frames and returns their format.
func SplitADTS(data []byte) (frames [][]byte, rate, channels int, err error) {
	rates := [...]int{96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350}
	for len(data) > 0 {
		if len(data) < 7 || data[0] != 0xff || data[1]&0xf0 != 0xf0 {
			return nil, 0, 0, errors.New("invalid ADTS header")
		}
		length := int(data[3]&3)<<11 | int(data[4])<<3 | int(data[5]>>5)
		index := int(data[2]>>2) & 15
		if length < 7 || length > len(data) || index >= len(rates) {
			return nil, 0, 0, errors.New("invalid ADTS frame")
		}
		frameRate, frameChannels := rates[index], int(data[2]&1)<<2|int(data[3]>>6)
		if rate != 0 && (rate != frameRate || channels != frameChannels) {
			return nil, 0, 0, errors.New("ADTS format changes")
		}
		rate, channels = frameRate, frameChannels
		frames = append(frames, data[:length:length])
		data = data[length:]
	}
	if len(frames) == 0 {
		return nil, 0, 0, errors.New("empty ADTS data")
	}
	return frames, rate, channels, nil
}

// ToneADTS encodes a mono sine tone as AAC-LC ADTS with FFmpeg. It is the only
// fixture that needs an external encoder; callers must treat a missing FFmpeg
// as a missing integration prerequisite.
func ToneADTS(ffmpeg string, rate int, hz int, duration time.Duration) ([][]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, ffmpeg, "-hide_banner", "-loglevel", "error",
		"-f", "lavfi", "-i", fmt.Sprintf("sine=frequency=%d:sample_rate=%d:duration=%.3f", hz, rate, duration.Seconds()),
		"-ac", "1", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "32k", "-f", "adts", "pipe:1")
	var stderr bytes.Buffer
	command.Stderr = &stderr
	output, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("generate AAC tone: %w: %s", err, stderr.String())
	}
	frames, _, _, err := SplitADTS(output)
	return frames, err
}

// SilentADTS returns n structurally valid AAC-LC ADTS frames with an opaque
// payload. They parse like real frames but are not guaranteed to decode.
func SilentADTS(rate, channels, n int) [][]byte {
	index := map[int]int{48000: 3, 44100: 4, 32000: 5, 24000: 6, 22050: 7, 16000: 8, 12000: 9, 11025: 10, 8000: 11}[rate]
	if index == 0 && rate != 96000 {
		panic("netvuetest: unsupported AAC sample rate")
	}
	payload := []byte{0x21, 0x10, 0x04, 0x60, 0x8c, 0x1c}
	length := 7 + len(payload)
	frames := make([][]byte, n)
	for i := range frames {
		frames[i] = append([]byte{
			0xff, 0xf1, byte(1<<6 | index<<2 | channels>>2),
			byte(channels&3)<<6 | byte(length>>11),
			byte(length >> 3), byte(length<<5) | 0x1f, 0xfc,
		}, payload...)
	}
	return frames
}
