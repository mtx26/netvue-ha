import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import MagicMock, patch

from netvue_bridge.discovery import DiscoveredCamera
from netvue_bridge.protocol import ConnectionClosed, ProtocolError, command_packet, read_packet
from netvue_bridge.rtsp import H264Publisher, TimestampClock, annexb_nals, validate_local_rtsp_url
from netvue_bridge.stream import StreamInterrupted, receive_stream, run_sessions


class RtspTests(unittest.TestCase):
    def test_only_local_rtsp_is_allowed(self):
        validate_local_rtsp_url("rtsp://127.0.0.1:8554/netvue2")
        for url in ("rtsp://example.com:8554/netvue2", "rtsp://0.0.0.0:8554/x",
                    "rtsp://127.0.0.1/x", "rtsp://user@127.0.0.1:8554/x"):
            with self.assertRaises(ValueError):
                validate_local_rtsp_url(url)

    def test_nal_boundaries_and_wraparound(self):
        data = b"\x00\x00\x00\x01\x67abc\x00\x00\x01\x68def"
        self.assertEqual(annexb_nals(data), [b"\x67abc", b"\x68def"])
        with self.assertRaises(ProtocolError):
            annexb_nals(b"not annex b")
        clock = TimestampClock()
        self.assertEqual([clock.update(n) for n in (0xFFFFFFFE, 1, 21)], [0, 3, 23])
        with self.assertRaises(ProtocolError):
            clock.update(20)

    def test_missing_channel_confirmation_is_rejected(self):
        left, right = socket.socketpair()
        try:
            body = b"\x11" + (0).to_bytes(4, "little") + b"\x00\x00\x01\x65x"
            right.sendall(struct.pack("<IIIHH", len(body), 0, 0, 0, 201) + body)
            with self.assertRaisesRegex(ProtocolError, "before LIVE confirmation"):
                receive_stream(left, object(), 1)
        finally:
            left.close()
            right.close()

    def test_waits_for_parameters_and_idr_and_bounds_startup(self):
        decoder = MagicMock()
        decoder.decode.return_value = []
        fake_av = types.SimpleNamespace(
            CodecContext=types.SimpleNamespace(create=MagicMock(return_value=decoder)),
            Packet=lambda data: data,
            error=types.SimpleNamespace(FFmpegError=ValueError),
        )
        with patch.dict(sys.modules, {"av": fake_av}):
            publisher = H264Publisher("rtsp://127.0.0.1:8554/netvue2")
            publisher.push(b"\x00\x00\x01\x67sps", 0)
            publisher.push(b"\x00\x00\x01\x68pps", 0)
            publisher.push(b"\x00\x00\x01\x41nonkey", 0)
            self.assertFalse(publisher.started)
            publisher.push(b"\x00\x00\x01\x65idr", 100)
            self.assertTrue(publisher.started)
            self.assertTrue(publisher.pending[0][0].startswith(
                b"\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps"))
            for index in range(1, 128):
                publisher.push(b"\x00\x00\x01\x41next", 100 + index)
            with self.assertRaisesRegex(ProtocolError, "buffer limit"):
                publisher.push(b"\x00\x00\x01\x41next", 228)
            publisher.close()
            self.assertEqual(publisher.pending_bytes, 0)

    def test_controlled_reconnection_and_no_retry_on_auth_failure(self):
        camera = DiscoveredCamera("192.0.2.1", 5555, "fake-key")
        publisher = MagicMock()
        with patch("netvue_bridge.stream.discover", return_value=camera), \
             patch("netvue_bridge.stream.connect", return_value=MagicMock()), \
             patch("netvue_bridge.stream.authenticate", return_value=0) as auth, \
             patch("netvue_bridge.stream.H264Publisher", return_value=publisher), \
             patch("netvue_bridge.stream.receive_stream",
                   side_effect=[StreamInterrupted("stalled"), None]), \
             patch("builtins.print"):
            self.assertEqual(run_sessions("source", "target", "rtsp://127.0.0.1:8554/netvue2",
                                          10, max_attempts=2, retry_delay=0), 0)
            self.assertEqual(auth.call_count, 2)
            self.assertEqual(publisher.close.call_count, 2)
        with patch("netvue_bridge.stream.discover", return_value=camera), \
             patch("netvue_bridge.stream.connect", return_value=MagicMock()), \
             patch("netvue_bridge.stream.authenticate", return_value=5) as auth, \
             patch("builtins.print"):
            self.assertEqual(run_sessions("source", "target", "rtsp://127.0.0.1:8554/netvue2",
                                          10, max_attempts=2, retry_delay=0), 1)
            self.assertEqual(auth.call_count, 1)
        with patch("netvue_bridge.stream.discover", return_value=camera), \
             patch("netvue_bridge.stream.connect", return_value=MagicMock()), \
             patch("netvue_bridge.stream.authenticate",
                   side_effect=ConnectionClosed("closed")) as auth, \
             patch("builtins.print"):
            self.assertEqual(run_sessions("source", "target", "rtsp://127.0.0.1:8554/netvue2",
                                          10, max_attempts=2, retry_delay=0), 1)
            self.assertEqual(auth.call_count, 1)

    @unittest.skipUnless(os.environ.get("NETVUE_MEDIAMTX") and os.environ.get("NETVUE_FFPROBE"),
                         "requires portable MediaMTX, FFprobe, and PyAV")
    def test_synthetic_camera_to_rtsp_ffprobe(self):
        import av

        config = Path(__file__).resolve().parents[1] / "netvue_bridge" / "mediamtx.local.yml"
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        temp = tempfile.TemporaryDirectory()
        test_config = Path(temp.name) / "mediamtx.yml"
        test_config.write_text(config.read_text().replace(":8554", f":{port}"))
        url = f"rtsp://127.0.0.1:{port}/netvue2"
        server = subprocess.Popen([os.environ["NETVUE_MEDIAMTX"], str(test_config)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        left, right = socket.socketpair()
        failures = []
        publisher = H264Publisher(url)

        def produce():
            try:
                read_packet(right, time.monotonic() + 3)
                right.sendall(command_packet([{"A": 3, "V": 2, "C": 0}]))
                codec = av.CodecContext.create("libx264", "w")
                codec.width, codec.height, codec.pix_fmt = 64, 64, "yuv420p"
                codec.time_base = Fraction(1, 10)
                codec.options = {"preset": "ultrafast", "tune": "zerolatency",
                                 "x264-params": "keyint=10:min-keyint=10:scenecut=0:bframes=0"}
                codec.open()
                for index in range(55):
                    frame = av.VideoFrame(64, 64, "yuv420p")
                    frame.pts = index
                    for plane in frame.planes:
                        plane.update(bytes(plane.buffer_size))
                    for packet in codec.encode(frame):
                        body = bytes([0x10 | int(packet.is_keyframe)]) + \
                            (index * 100).to_bytes(4, "little") + bytes(packet)
                        right.sendall(struct.pack("<IIIHH", len(body), 0, 0, 0, 201) + body)
                    time.sleep(0.1)
                time.sleep(1)
            except (OSError, ProtocolError, TimeoutError) as exc:
                failures.append(type(exc).__name__)

        def consume():
            try:
                receive_stream(left, publisher, 6)
            except (OSError, ProtocolError, TimeoutError, av.error.FFmpegError) as exc:
                failures.append(type(exc).__name__ + ": " + str(exc))

        writer = threading.Thread(target=produce)
        reader = threading.Thread(target=consume)
        player = None
        try:
            deadline = time.monotonic() + 5
            while True:
                if server.poll() is not None:
                    self.fail("MediaMTX failed to start: " + server.stderr.read().decode())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("MediaMTX did not become ready")
                    time.sleep(0.05)
            writer.start()
            reader.start()
            while publisher.published_frames < 1 and reader.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertGreater(publisher.published_frames, 0, str(failures))
            ffmpeg = str(Path(os.environ["NETVUE_FFPROBE"]).with_name("ffmpeg"))
            player = subprocess.Popen(
                [ffmpeg, "-v", "error", "-rtsp_transport", "tcp", "-i", url,
                 "-t", "2", "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            probe = subprocess.run(
                [os.environ["NETVUE_FFPROBE"], "-v", "error", "-rtsp_transport", "tcp",
                 "-select_streams", "v:0", "-show_entries",
                 "stream=codec_name,width,height:packet=pts_time,flags",
                 "-show_streams", "-show_packets", "-read_intervals", "%+2",
                 "-of", "json", url],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, check=True,
            )
            result = json.loads(probe.stdout)
            stream = result["streams"][0]
            self.assertEqual((stream["codec_name"], stream["width"], stream["height"]),
                             ("h264", 64, 64))
            packets = result["packets"]
            self.assertTrue(all("pts_time" in p for p in packets[1:]), str(packets[:3]))
            times = [float(p["pts_time"]) for p in packets if "pts_time" in p]
            self.assertGreaterEqual(len(times), 10)
            self.assertTrue(all(b > a for a, b in zip(times, times[1:])))
            self.assertTrue(all(abs((b - a) - 0.1) < 0.005
                                for a, b in zip(times, times[1:])))
            self.assertTrue(any("K" in p["flags"] for p in packets))
            _, player_error = player.communicate(timeout=6)
            self.assertEqual(player.returncode, 0, player_error.decode())
            reader.join(7)
            self.assertFalse(reader.is_alive())
            self.assertFalse(failures, str(failures))
            self.assertGreaterEqual(publisher.published_frames, 50)
            print(f"synthetic RTSP: {len(times)} packets; {publisher.published_frames} "
                  f"published; {publisher.key_frames} keyframes")
        finally:
            if player is not None and player.poll() is None:
                player.terminate()
                player.communicate(timeout=5)
            left.close()
            right.close()
            if writer.ident is not None:
                writer.join(7)
            if reader.ident is not None:
                reader.join(7)
            publisher.close()
            server.terminate()
            server.wait(timeout=5)
            server.stderr.close()
            temp.cleanup()

    @unittest.skipUnless(os.environ.get("NETVUE_MEDIAMTX") and os.environ.get("NETVUE_FFPROBE"),
                         "requires portable MediaMTX, FFmpeg and PyAV")
    def test_bounded_check_with_synthetic_camera(self):
        import av

        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        udp.settimeout(4)
        tcp.settimeout(4)
        udp.bind(("127.0.0.2", 36868))
        tcp.bind(("127.0.0.2", 5555))
        tcp.listen(1)
        counts = []
        failures = []

        def camera():
            try:
                _, sender = udp.recvfrom(65535)
                udp.sendto(command_packet([{"A": 8, "V": 2, "C": 0, "P": [
                    "fake-id", "fake-fw", "127.0.0.2", 5555, "fake-key"
                ]}]), sender)
                sock, _ = tcp.accept()
                with sock:
                    read_packet(sock, time.monotonic() + 3)
                    counts.append("login")
                    sock.sendall(command_packet([{"A": 1, "V": 1, "C": 0}]))
                    read_packet(sock, time.monotonic() + 3)
                    sock.sendall(command_packet([{"A": 3, "V": 2, "C": 0}]))
                    codec = av.CodecContext.create("libx264", "w")
                    codec.width, codec.height, codec.pix_fmt = 64, 64, "yuv420p"
                    codec.time_base = Fraction(1, 10)
                    codec.options = {"preset": "ultrafast", "tune": "zerolatency",
                                     "x264-params": "keyint=10:min-keyint=10:scenecut=0:bframes=0"}
                    codec.open()
                    for index in range(90):
                        frame = av.VideoFrame(64, 64, "yuv420p")
                        frame.pts = index
                        for plane in frame.planes:
                            plane.update(bytes(plane.buffer_size))
                        for packet in codec.encode(frame):
                            body = bytes([0x10 | int(packet.is_keyframe)]) + \
                                (index * 100).to_bytes(4, "little") + bytes(packet)
                            sock.sendall(struct.pack("<IIIHH", len(body), 0, 0, 0, 201) + body)
                        time.sleep(0.1)
                    time.sleep(1.5)
            except (OSError, ProtocolError, TimeoutError, av.error.FFmpegError) as exc:
                failures.append(type(exc).__name__)

        thread = threading.Thread(target=camera)
        thread.start()
        try:
            result = subprocess.run(
                [sys.executable, "-B", "-m", "netvue_bridge.check_rtsp",
                 "--source", "127.0.0.1", "--target", "127.0.0.2",
                 "--mediamtx", os.environ["NETVUE_MEDIAMTX"],
                 "--ffprobe", os.environ["NETVUE_FFPROBE"],
                 "--ffmpeg", str(Path(os.environ["NETVUE_FFPROBE"]).with_name("ffmpeg"))],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"decoded_frames":', result.stdout)
            self.assertEqual(counts, ["login"])
            print("synthetic bounded check:", result.stdout.strip())
        finally:
            udp.close()
            tcp.close()
            thread.join(12)
            self.assertFalse(thread.is_alive())
            self.assertFalse(failures, str(failures))


if __name__ == "__main__":
    unittest.main()
