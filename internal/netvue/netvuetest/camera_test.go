package netvuetest

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"net"
	"os/exec"
	"strconv"
	"testing"
	"time"
)

const testGroup = 201 // loopback 127.201.x.y; other packages use other groups

func within(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	t.Cleanup(cancel)
	return ctx
}

// dial performs discovery and returns an open TCP connection.
func dial(t *testing.T, c *Camera) net.Conn {
	t.Helper()
	conn, err := net.Dial("tcp4", net.JoinHostPort(c.Host(), strconv.Itoa(c.Port())))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	return conn
}

func send(t *testing.T, conn net.Conn, unit map[string]any) {
	t.Helper()
	if _, err := conn.Write(Command(unit)); err != nil {
		t.Fatal(err)
	}
}

func readUnit(t *testing.T, conn net.Conn) unit {
	t.Helper()
	_ = conn.SetReadDeadline(time.Now().Add(10 * time.Second))
	flag, kind, body, err := ReadPacket(conn)
	if err != nil || flag != 0 || kind != kindCommand {
		t.Fatalf("read command: flag=%d kind=%d err=%v", flag, kind, err)
	}
	u, ok := firstUnit(body)
	if !ok {
		t.Fatalf("invalid command %q", body)
	}
	return u
}

func login(t *testing.T, conn net.Conn, key string) unit {
	t.Helper()
	send(t, conn, map[string]any{"Q": 1, "V": 1, "P": []any{key, 7, "localUser", "127.0.0.1", 1}})
	return readUnit(t, conn)
}

func liveRequest(audio bool) map[string]any {
	channels := [][]int{{1, 0}}
	if audio {
		channels = append(channels, []int{2, 0})
	}
	return map[string]any{"Q": 3, "V": 2, "P": []any{1, channels, []int64{1, 2}}}
}

func code(u unit) int {
	if u.C == nil {
		return -1
	}
	return *u.C
}

func TestDiscoveryLoginLiveAndMedia(t *testing.T) {
	frames := SolidH264(64, 64, 0, 0, 255, 3)
	c := Start(t, Options{Host: AllocHost(testGroup), Audio: true, Video: frames, FrameInterval: 10 * time.Millisecond})

	udp, err := net.Dial("udp4", net.JoinHostPort(c.Host(), "36868"))
	if err != nil {
		t.Fatal(err)
	}
	defer udp.Close()
	if _, err := udp.Write(Command(map[string]any{"Q": 8, "V": 2, "P": []any{}})); err != nil {
		t.Fatal(err)
	}
	_ = udp.SetReadDeadline(time.Now().Add(5 * time.Second))
	buffer := make([]byte, 2048)
	n, err := udp.Read(buffer)
	if err != nil {
		t.Fatal(err)
	}
	var reply []struct {
		A, V, C int
		P       []json.RawMessage
	}
	if err := json.Unmarshal(buffer[headerSize:n], &reply); err != nil || len(reply) != 1 || reply[0].A != 8 || reply[0].V != 2 || len(reply[0].P) != 6 {
		t.Fatalf("discovery reply %q: %v", buffer[headerSize:n], err)
	}
	var announcedPort int
	_ = json.Unmarshal(reply[0].P[3], &announcedPort)
	if announcedPort != c.Port() {
		t.Fatalf("announced port %d, listening on %d", announcedPort, c.Port())
	}

	conn := dial(t, c)
	if ack := login(t, conn, DefaultKey); ack.A != 1 || code(ack) != 0 {
		t.Fatalf("login ack %+v", ack)
	}
	send(t, conn, liveRequest(true))
	if ack := readUnit(t, conn); ack.A != 3 || len(ack.P) != 2 {
		t.Fatalf("LIVE ack %+v", ack)
	}
	// Media: video flows at once, audio only after a direction request.
	var previous uint32
	for i := 0; i < 3; i++ {
		flag, kind, body, err := ReadPacket(conn)
		if err != nil || flag != 0 || kind != kindMedia || body[0]>>4 != 1 {
			t.Fatalf("expected video packet: %d %d %v", flag, kind, err)
		}
		pts := binary.LittleEndian.Uint32(body[1:5])
		if i > 0 && pts != previous+10 {
			t.Fatalf("video PTS step %d -> %d, want +10", previous, pts)
		}
		previous = pts
		if !bytes.Equal(body[5:], frames[i].Data) {
			t.Fatalf("frame %d differs from the fixture", i)
		}
	}
	send(t, conn, map[string]any{"Q": 5, "V": 1, "P": []int{1}})
	for sawAudio := false; !sawAudio; {
		_, _, body, err := ReadPacket(conn)
		if err != nil {
			t.Fatal(err)
		}
		if body[0]>>4 == 2 {
			sawAudio = true
		}
	}
	if s := c.State(); s.Logins != 1 || s.LiveRequests != 1 || len(s.Directions) != 1 {
		t.Fatalf("state %+v", s)
	}
}

func TestRejectedLoginsAndWrongKey(t *testing.T) {
	c := Start(t, Options{Host: AllocHost(testGroup), RejectLogins: 1})
	first := dial(t, c)
	if ack := login(t, first, DefaultKey); ack.A != 1 || code(ack) != 5 {
		t.Fatalf("ack %+v", ack)
	}
	if _, _, _, err := ReadPacket(first); err == nil {
		t.Fatal("rejected login kept the connection open")
	}
	ctx := within(t)
	if err := c.Wait(ctx, "first connection closed", func(s Snapshot) bool { return s.Logins == 1 && s.Connections == 0 }); err != nil {
		t.Fatal(err)
	}
	wrong := dial(t, c)
	if ack := login(t, wrong, "other-key"); code(ack) != 5 {
		t.Fatalf("wrong key accepted: %+v", ack)
	}
	second := dial(t, c)
	if ack := login(t, second, DefaultKey); code(ack) != 0 {
		t.Fatalf("second login rejected: %+v", ack)
	}
	send(t, second, liveRequest(false))
	if ack := readUnit(t, second); ack.A != 3 {
		t.Fatalf("second login did not reach LIVE: %+v", ack)
	}
}

func TestFaultsDropAndVideoChange(t *testing.T) {
	c := Start(t, Options{Host: AllocHost(testGroup), FrameInterval: 5 * time.Millisecond})

	for _, stage := range []string{"login", "live", "media"} {
		for _, fault := range faultModes[stage] {
			if fault == "login-timeout" || fault == "stall" {
				continue // the peer's own timeout ends these; covered by integration tests
			}
			if err := c.FailNext(stage, fault); err != nil {
				t.Fatal(err)
			}
			if err := c.FailNext(stage, fault); err == nil {
				t.Fatal("second pending fault accepted")
			}
			conn := dial(t, c)
			send(t, conn, map[string]any{"Q": 1, "V": 1, "P": []any{DefaultKey, 7, "localUser", "127.0.0.1", 1}})
			if stage != "login" {
				readUnit(t, conn)
				send(t, conn, liveRequest(false))
			}
			_ = conn.SetReadDeadline(time.Now().Add(10 * time.Second))
			for {
				if _, _, _, err := ReadPacket(conn); err != nil {
					break
				}
			}
			_ = conn.Close()
			if got := c.State().LastFault[stage]; got != fault {
				t.Fatalf("fault %s/%s not applied (last %q)", stage, fault, got)
			}
		}
	}
	if err := c.FailNext("media", "nope"); err == nil {
		t.Fatal("unknown fault accepted")
	}

	conn := dial(t, c)
	login(t, conn, DefaultKey)
	send(t, conn, liveRequest(false))
	readUnit(t, conn)
	ctx := within(t)
	if err := c.Wait(ctx, "streaming", func(s Snapshot) bool { return s.Streaming == 1 && s.VideoFrames > 0 }); err != nil {
		t.Fatal(err)
	}
	c.SetVideo(SolidH264(96, 64, 255, 0, 0, 2))
	want := SolidH264(96, 64, 255, 0, 0, 1)[0].Data
	_ = conn.SetReadDeadline(time.Now().Add(10 * time.Second))
	for {
		_, _, body, err := ReadPacket(conn)
		if err != nil {
			t.Fatal(err)
		}
		if bytes.Equal(body[5:], want) {
			break
		}
	}
	c.Drop()
	if err := c.Wait(ctx, "drop", func(s Snapshot) bool { return s.Connections == 0 && s.Streaming == 0 }); err != nil {
		t.Fatal(err)
	}
}

func TestTalkbackFramesAreRecorded(t *testing.T) {
	c := Start(t, Options{Host: AllocHost(testGroup), Audio: true})
	conn := dial(t, c)
	login(t, conn, DefaultKey)
	send(t, conn, liveRequest(true))
	readUnit(t, conn)
	send(t, conn, map[string]any{"Q": 5, "V": 1, "P": []int{3}})
	adts := SilentADTS(8000, 1, 2)
	for i, frame := range adts {
		body := append([]byte{0x20, byte(i), 0, 0, 0}, frame...)
		if _, err := conn.Write(Packet(kindMedia, 0, body)); err != nil {
			t.Fatal(err)
		}
	}
	if err := c.Wait(within(t), "talk frames", func(s Snapshot) bool { return s.TalkFrames == 2 }); err != nil {
		t.Fatal(err)
	}
	talk := c.Talk()
	if talk[1].PTS != 1 || !bytes.Equal(talk[0].Payload, adts[0]) {
		t.Fatalf("recorded talk frames %+v", talk)
	}
	if frames, rate, channels, err := SplitADTS(append(talk[0].Payload, talk[1].Payload...)); err != nil || len(frames) != 2 || rate != 8000 || channels != 1 {
		t.Fatalf("ADTS round trip: %v %d %d %d", err, len(frames), rate, channels)
	}
}

// The fixture is the base of every decode check, so verify it with an
// independent decoder.
func TestSolidH264DecodesWithFFmpeg(t *testing.T) {
	ffmpeg := Executable(t, "NETVUE_TEST_FFMPEG", "ffmpeg")
	for _, geometry := range [][2]int{{64, 64}, {96, 64}, {320, 192}} {
		for name, color := range map[string][3]byte{"red": {255, 0, 0}, "blue": {0, 0, 255}} {
			var stream bytes.Buffer
			for _, f := range SolidH264(geometry[0], geometry[1], color[0], color[1], color[2], 3) {
				stream.Write(f.Data)
			}
			command := exec.CommandContext(within(t), ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "h264", "-i", "pipe:0",
				"-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1")
			command.Stdin = &stream
			var stderr bytes.Buffer
			command.Stderr = &stderr
			output, err := command.Output()
			if err != nil {
				t.Fatalf("%dx%d %s: %v: %s", geometry[0], geometry[1], name, err, stderr.String())
			}
			size := geometry[0] * geometry[1] * 3
			if len(output) != 3*size {
				t.Fatalf("%dx%d %s decoded %d bytes, want 3 frames of %d", geometry[0], geometry[1], name, len(output), size)
			}
			p := output[:3]
			if (name == "red" && (p[0] < 235 || p[1] > 20 || p[2] > 20)) || (name == "blue" && (p[2] < 235 || p[0] > 20 || p[1] > 20)) {
				t.Fatalf("%dx%d %s decoded RGB=%v", geometry[0], geometry[1], name, p)
			}
		}
	}
}

func TestToneADTSDecodesWithFFmpeg(t *testing.T) {
	ffmpeg := Executable(t, "NETVUE_TEST_FFMPEG", "ffmpeg")
	frames, err := ToneADTS(ffmpeg, 8000, 440, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	if len(frames) < 7 {
		t.Fatalf("only %d frames", len(frames))
	}
	command := exec.CommandContext(within(t), ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "aac", "-i", "pipe:0",
		"-f", "s16le", "pipe:1")
	command.Stdin = bytes.NewReader(bytes.Join(frames, nil))
	output, err := command.Output()
	if err != nil || len(output) < 8000 {
		t.Fatalf("decode: %v, %d bytes", err, len(output))
	}
	var peak int16
	for i := 0; i+1 < len(output); i += 2 {
		if v := int16(binary.LittleEndian.Uint16(output[i:])); v > peak {
			peak = v
		}
	}
	if peak < 1000 {
		t.Fatalf("decoded tone is silent (peak %d)", peak)
	}
}
