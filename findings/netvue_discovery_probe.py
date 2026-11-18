"""Bounded Netvue discovery and passive TCP banner diagnostics."""

import argparse
import json
import re
import socket
import struct
import time
import unittest
from unittest.mock import patch


DISCOVERY_PORT = 36868
BODY = b'[{"Q":8,"V":2,"P":[]}]'
REQUEST = struct.pack("<IIIHH", len(BODY), 0, 0, 0, 200) + BODY


def decode_response(data, source=None):
    if len(data) < 16:
        return {"status": "short_header", "bytes": len(data)}
    length, _, _, flag, packet_type = struct.unpack_from("<IIIHH", data)
    if length <= 0 or length >= 1048576 or length > len(data) - 16:
        return {"status": "invalid_length", "bytes": len(data)}
    if packet_type != 200:
        return {"status": "unexpected_packet_type", "type": packet_type}
    if flag != 0:
        return {"status": "encrypted_or_flagged", "flag": flag}
    try:
        units = json.loads(data[16 : 16 + length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "invalid_json"}
    if not isinstance(units, list):
        return {"status": "unexpected_json"}
    for unit in units:
        if not isinstance(unit, dict) or unit.get("A") != 8:
            continue
        version = unit.get("V")
        params = unit.get("P")
        if version not in (1, 2) or not isinstance(params, list) or len(params) < 5:
            return {"status": "invalid_discovery_ack"}
        result = {"status": "discovery_ack", "version": version}
        firmware = params[1]
        if isinstance(firmware, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,48}", firmware):
            result["firmware"] = firmware
        port = params[3]
        if type(port) is int and 1 <= port <= 65535:
            result["announced_port"] = port
        if source is not None and isinstance(params[2], str):
            result["announced_host_matches_sender"] = params[2] == source
        if version == 2 and len(params) > 5 and isinstance(params[5], list):
            caps = params[5]
            for index, name in enumerate(("audio", "flip", "light", "sdcard")):
                if index < len(caps) and type(caps[index]) is bool:
                    result[name] = caps[index]
            if len(params) > 6 and isinstance(params[6], dict):
                result["ability_object_present"] = True
            if len(params) > 7 and type(params[7]) is int:
                result["client_version"] = params[7]
        return result
    return {"status": "not_discovery_ack"}


def receive(sock, targets, timeout, expected=None, on_response=None):
    expected = targets if expected is None else expected
    deadline = time.monotonic() + timeout
    seen = set()
    while time.monotonic() < deadline:
        sock.settimeout(max(0.01, deadline - time.monotonic()))
        try:
            data, (host, _) = sock.recvfrom(65535)
        except socket.timeout:
            break
        if host not in expected or host in seen:
            continue
        seen.add(host)
        result = decode_response(data, host)
        print(json.dumps({"camera": targets.index(host) + 1, **result}), flush=True)
        if on_response is not None:
            on_response(host, result)
    for index, host in enumerate(targets, 1):
        if host in expected and host not in seen:
            print(json.dumps({"camera": index, "status": "no_udp_response"}), flush=True)


def discover(targets, broadcast, source, on_response=None):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((source, 0))
        if broadcast:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(REQUEST, (broadcast, DISCOVERY_PORT))
            print("sent: one local-subnet broadcast, discovery V2 UDP/36868", flush=True)
            receive(sock, targets, 2, on_response=on_response)
        else:
            for index, host in enumerate(targets, 1):
                sock.sendto(REQUEST, (host, DISCOVERY_PORT))
                print(f"sent: camera {index}, one unicast discovery V2 UDP/36868", flush=True)
                receive(sock, targets, 2, [host], on_response)
                time.sleep(0.5)


def tcp_test(targets, camera):
    for index, host in enumerate(targets, 1):
        if camera is not None and camera != index:
            continue
        try:
            with socket.create_connection((host, 5555), timeout=2) as sock:
                sock.settimeout(2)
                try:
                    banner = sock.recv(256)
                except socket.timeout:
                    print(f"camera {index}: tcp_connected; no_unsolicited_banner", flush=True)
                else:
                    print(f"camera {index}: tcp_connected; unsolicited_bytes={len(banner)}", flush=True)
        except OSError as exc:
            print(f"camera {index}: tcp_error={type(exc).__name__} errno={exc.errno}", flush=True)
        time.sleep(0.5)


class ProbeTests(unittest.TestCase):
    def test_request_exact(self):
        self.assertEqual(REQUEST, bytes.fromhex("1600000000000000000000000000c800") + BODY)
        self.assertEqual(json.loads(REQUEST[16:]), [{"Q": 8, "V": 2, "P": []}])

    def test_safe_ack(self):
        body = json.dumps([{"A": 8, "V": 2, "P": [
            "sensitive-id", "1.2.3", "sensitive-address", 1234, "sensitive-key",
            [True, False, True, False, "sensitive-ssid", 42],
            {"private": "sensitive"}, 3, "sensitive-token",
        ]}]).encode()
        result = decode_response(struct.pack("<IIIHH", len(body), 0, 0, 0, 200) + body)
        self.assertEqual(result["status"], "discovery_ack")
        self.assertEqual(result["announced_port"], 1234)
        self.assertEqual(result["audio"], True)
        self.assertNotIn("sensitive", json.dumps(result))

    def test_broadcast_sends_once(self):
        with patch("socket.socket") as factory, patch("builtins.print"):
            sock = factory.return_value.__enter__.return_value
            sock.recvfrom.side_effect = socket.timeout
            discover(["192.0.2.1", "192.0.2.2"], "192.0.2.255", "192.0.2.10")
            sock.sendto.assert_called_once_with(REQUEST, ("192.0.2.255", DISCOVERY_PORT))

    def test_malformed_and_encrypted(self):
        self.assertEqual(decode_response(b"x")["status"], "short_header")
        self.assertEqual(decode_response(struct.pack("<IIIHH", 5, 0, 0, 1, 200) + b"12345")["status"], "encrypted_or_flagged")
        self.assertEqual(decode_response(struct.pack("<IIIHH", 99, 0, 0, 0, 200) + b"x")["status"], "invalid_length")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--mode", choices=("unicast", "broadcast", "tcp"))
    parser.add_argument("--targets", nargs=2)
    parser.add_argument("--source")
    parser.add_argument("--broadcast")
    parser.add_argument("--camera", type=int, choices=(1, 2))
    args = parser.parse_args()
    if args.self_test:
        unittest.main(argv=[__file__])
    elif not args.mode or not args.targets:
        parser.error("--mode and two --targets are required")
    elif args.mode == "tcp":
        tcp_test(args.targets, args.camera)
    elif not args.source or (args.mode == "broadcast" and not args.broadcast):
        parser.error("--source and, for broadcast, --broadcast are required")
    else:
        discover(args.targets, args.broadcast if args.mode == "broadcast" else None, args.source)


if __name__ == "__main__":
    main()
