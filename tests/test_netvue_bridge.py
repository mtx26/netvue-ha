import json
import socket
import struct
import sys
import time
import types
import unittest
from unittest.mock import patch

from netvue_bridge.authentication import authenticate, login_packet
from netvue_bridge.discovery import DiscoveredCamera, parse_discovery
from netvue_bridge.protocol import MAX_BODY, ProtocolError, command_packet, parse_datagram, read_packet
from netvue_bridge.video import VideoSummary, live_packet, receive_live


class ScriptedSocket:
    def __init__(self, data=b"", fragment=3):
        self.data = data
        self.fragment = fragment
        self.sent = []

    def settimeout(self, value):
        pass

    def sendall(self, value):
        self.sent.append(value)

    def recv(self, size):
        if not self.data:
            raise socket.timeout()
        length = min(size, self.fragment)
        result, self.data = self.data[:length], self.data[length:]
        return result


class BridgeTests(unittest.TestCase):
    def discovery_packet(self, host="192.0.2.1", key="fake-key", code=0):
        return command_packet([{"A": 8, "V": 2, "C": code, "P": [
            "fake-id", "fake-fw", host, 5555, key
        ]}])

    def test_discovery_validates_endpoint_and_never_repr_key(self):
        camera = parse_discovery(self.discovery_packet(), "192.0.2.1", "192.0.2.1")
        self.assertEqual(camera.port, 5555)
        self.assertNotIn("fake-key", repr(camera))
        with self.assertRaises(ProtocolError):
            parse_discovery(self.discovery_packet("192.0.2.2"), "192.0.2.1", "192.0.2.1")
        with self.assertRaises(ProtocolError):
            parse_discovery(self.discovery_packet(key=""), "192.0.2.1", "192.0.2.1")
        with self.assertRaises(ProtocolError):
            parse_discovery(self.discovery_packet(code=1), "192.0.2.1", "192.0.2.1")

    def test_login_one_attempt_requires_explicit_result(self):
        camera = DiscoveredCamera("192.0.2.1", 5555, "fake-key")
        for ack, expected in (({"A": 1, "V": 1, "C": 0}, 0), ({"A": 1, "V": 1, "C": 5}, 5)):
            sock = ScriptedSocket(command_packet([ack]))
            self.assertEqual(authenticate(sock, camera), expected)
            self.assertEqual(len(sock.sent), 1)
        sock = ScriptedSocket(command_packet([{"A": 1, "V": 1}]))
        with self.assertRaises(ProtocolError):
            authenticate(sock, camera)
        self.assertEqual(len(sock.sent), 1)
        kind, body = parse_datagram(login_packet(camera))
        unit = json.loads(body)[0]
        self.assertEqual((kind, unit["Q"], unit["V"]), (200, 1, 1))
        self.assertEqual(unit["P"][0], "fake-key")
        self.assertEqual(unit["P"][2:], ["localUser", "192.0.2.1", 5555])

    def test_stream_framing_and_limits(self):
        sock = ScriptedSocket(command_packet([{"A": 1, "V": 1, "C": 0}]), fragment=2)
        kind, body = read_packet(sock, time.monotonic() + 1)
        self.assertEqual(kind, 200)
        self.assertEqual(json.loads(body)[0]["C"], 0)
        with self.assertRaises(ProtocolError):
            parse_datagram(struct.pack("<IIIHH", 1, 0, 0, 1, 200) + b"x")

    def test_datagram_and_stream_reject_invalid_headers(self):
        for length, flag, expected in ((0, 0, "Invalid packet size"),
                                       (MAX_BODY, 0, "Invalid packet size"),
                                       (1, 1, "Encrypted or flagged packet unsupported")):
            packet = struct.pack("<IIIHH", length, 0, 0, flag, 200) + b"x"
            with self.subTest(length=length, flag=flag):
                with self.assertRaisesRegex(ProtocolError, expected):
                    parse_datagram(packet)
                with self.assertRaisesRegex(ProtocolError, expected):
                    read_packet(ScriptedSocket(packet), time.monotonic() + 1)
        with self.assertRaisesRegex(ProtocolError, "Invalid packet size"):
            parse_datagram(struct.pack("<IIIHH", 2, 0, 0, 1, 200) + b"x")

    def test_live_request_and_frame_metadata_only(self):
        _, body = parse_datagram(live_packet(100))
        self.assertEqual(json.loads(body), [{"Q": 3, "V": 2, "P": [1, [[1, 0]], [100, 31536100]]}])
        payload = b"\x11" + (1234).to_bytes(4, "little") + b"\x00\x00\x00\x01\x65fake"
        packet = struct.pack("<IIIHH", len(payload), 0, 0, 0, 201) + payload
        sock = ScriptedSocket(command_packet([{"A": 3, "V": 2, "C": 0}]) + packet)
        summary = receive_live(sock, duration=0.1)
        self.assertEqual((summary.channel_result, summary.video_frames, summary.key_frames), (0, 1, 1))
        self.assertEqual(summary.first_pts, 1234)
        self.assertEqual(len(sock.sent), 1)
        self.assertNotIn("fake", repr(summary))
        with self.assertRaises(ValueError):
            receive_live(sock, duration=11)

    def test_bad_media_fails_explicitly(self):
        summary = VideoSummary()
        with self.assertRaises(ProtocolError):
            summary.add_frame(b"\x10")
        with self.assertRaises(ProtocolError):
            summary.add_frame(b"\xf0\x00\x00\x00\x00")

    def test_in_memory_decode_reports_only_dimensions(self):
        frame = types.SimpleNamespace(width=640, height=480)
        container = unittest.mock.MagicMock()
        container.__enter__.return_value.decode.return_value = iter([frame])
        fake_av = types.SimpleNamespace(
            open=unittest.mock.Mock(return_value=container),
            error=types.SimpleNamespace(FFmpegError=ValueError),
        )
        summary = VideoSummary()
        summary.add_frame(b"\x11\x00\x00\x00\x00synthetic")
        with patch.dict(sys.modules, {"av": fake_av}):
            self.assertEqual(summary.test_h264_decode(), {
                "status": "decoded_h264_image_in_memory", "width": 640, "height": 480,
            })

if __name__ == "__main__":
    unittest.main()
