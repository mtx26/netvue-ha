"""Discovery and offline-only login preparation; never sends authentication."""

import argparse
import ipaddress
import json
import struct
import unittest

import netvue_discovery_probe as probe


def select_endpoint(source, result):
    if result.get("status") != "discovery_ack":
        raise ValueError("A valid discovery response is required")
    if result.get("announced_host_matches_sender") is not True:
        raise ValueError("Announced host does not match the response source")
    port = result.get("announced_port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid announced port")
    return source, port


def prepare_synthetic_login():
    unit = {"Q": 1, "V": 1, "P": ["simulation-key", 42, "simulation-user", "192.0.2.1", 5555]}
    body = json.dumps([unit], separators=(",", ":")).encode("utf-8")
    return struct.pack("<IIIHH", len(body), 0, 0, 0, 200) + body


def simulate_authentication(packet):
    if len(packet) < 16:
        raise ValueError("Invalid simulated packet")
    length, _, _, flag, kind = struct.unpack_from("<IIIHH", packet)
    if length != len(packet) - 16 or flag != 0 or kind != 200:
        raise ValueError("Invalid simulated packet header")
    try:
        units = json.loads(packet[16:].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid simulated packet body") from exc
    expected = {"Q": 1, "V": 1, "P": ["simulation-key", 42, "simulation-user", "192.0.2.1", 5555]}
    return units == [expected]


def synthetic_response():
    unit = {"A": 8, "V": 2, "P": [
        "simulation-id", "1.0", "192.0.2.1", 5555, "simulation-key",
        [True, False, True, False, "simulation-ssid", 1],
    ]}
    body = json.dumps([unit], separators=(",", ":")).encode("utf-8")
    return struct.pack("<IIIHH", len(body), 0, 0, 0, 200) + body


def demo():
    source = "192.0.2.1"
    response = probe.decode_response(synthetic_response(), source)
    _, port = select_endpoint(source, response)
    packet = prepare_synthetic_login()
    if not simulate_authentication(packet):
        raise RuntimeError("Synthetic authentication was rejected")
    print(f"simulation: discovery_ack V{response['version']}; selected port {port}")
    print(f"simulation: prepared {len(packet)} bytes; synthetic server accepted; nothing sent")


def live_discovery(source, targets, subnet, broadcast):
    network = ipaddress.IPv4Network(subnet, strict=True)
    source_ip = ipaddress.IPv4Address(source)
    target_ips = [ipaddress.IPv4Address(target) for target in targets]
    if source_ip not in network or any(target not in network for target in target_ips):
        raise ValueError("Source and targets must be in the supplied subnet")
    if len(set(target_ips)) != 2 or any(
        target in (network.network_address, network.broadcast_address) for target in target_ips
    ):
        raise ValueError("Two distinct unicast targets are required")

    def selected(host, result):
        try:
            _, port = select_endpoint(host, result)
        except ValueError as exc:
            print(f"camera {targets.index(host) + 1}: endpoint rejected: {exc}")
        else:
            print(f"camera {targets.index(host) + 1}: endpoint validated; port={port}")

    destination = str(network.broadcast_address) if broadcast else None
    probe.discover(targets, destination, source, on_response=selected)


class SimulationTests(unittest.TestCase):
    def test_synthetic_login_packet(self):
        packet = prepare_synthetic_login()
        length, seq, crc, flag, kind = struct.unpack_from("<IIIHH", packet)
        self.assertEqual((length, seq, crc, flag, kind), (len(packet) - 16, 0, 0, 0, 200))
        unit = json.loads(packet[16:])[0]
        self.assertEqual((unit["Q"], unit["V"]), (1, 1))
        self.assertEqual(unit["P"], ["simulation-key", 42, "simulation-user", "192.0.2.1", 5555])

    def test_endpoint_from_synthetic_discovery(self):
        result = probe.decode_response(synthetic_response(), "192.0.2.1")
        self.assertEqual(select_endpoint("192.0.2.1", result), ("192.0.2.1", 5555))
        self.assertNotIn("simulation-key", json.dumps(result))

    def test_reject_mismatched_host(self):
        result = probe.decode_response(synthetic_response(), "192.0.2.2")
        with self.assertRaises(ValueError):
            select_endpoint("192.0.2.2", result)

    def test_synthetic_authentication_is_offline_and_bounded(self):
        packet = prepare_synthetic_login()
        self.assertTrue(simulate_authentication(packet))
        self.assertFalse(simulate_authentication(packet.replace(b"simulation-user", b"simulation-fail")))
        with self.assertRaises(ValueError):
            simulate_authentication(packet[:-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--demo", action="store_true")
    mode.add_argument("--discover", choices=("unicast", "broadcast"))
    parser.add_argument("--source")
    parser.add_argument("--targets", nargs=2)
    parser.add_argument("--subnet")
    args = parser.parse_args()
    if args.self_test:
        unittest.main(argv=[__file__])
    elif args.demo:
        demo()
    elif not args.source or not args.targets or not args.subnet:
        parser.error("--discover requires --source, --targets and --subnet")
    else:
        try:
            live_discovery(args.source, args.targets, args.subnet, args.discover == "broadcast")
        except ValueError as exc:
            parser.error(str(exc))


if __name__ == "__main__":
    main()
