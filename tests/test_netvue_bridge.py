import json
import socket
import struct
import time
import unittest

from netvue_bridge.authentication import authenticate, login_packet
from netvue_bridge.discovery import DiscoveredCamera, parse_discovery
from netvue_bridge.protocol import MAX_BODY, ProtocolError, command_packet, parse_datagram, read_packet
from netvue_bridge.stream import StreamInterrupted, receive_stream


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

    def test_stream_sends_one_live_request(self):
        sock = ScriptedSocket()
        with self.assertRaises(StreamInterrupted):
            receive_stream(sock, object(), duration=0.1)
        self.assertEqual(len(sock.sent), 1)
        kind, body = parse_datagram(sock.sent[0])
        request = json.loads(body)[0]
        self.assertEqual((kind, request["Q"], request["V"], request["P"][:2]),
                         (200, 3, 2, [1, [[1, 0]]]))
        self.assertEqual(request["P"][2][1] - request["P"][2][0], 31536000)

if __name__ == "__main__":
    unittest.main()
