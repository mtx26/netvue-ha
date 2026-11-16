import ipaddress
import socket
import time
from dataclasses import dataclass, field

from .protocol import ProtocolError, command_packet, parse_datagram, parse_units


@dataclass(frozen=True)
class DiscoveredCamera:
    host: str = field(repr=False)
    port: int
    key: str = field(repr=False)


def parse_discovery(data: bytes, source: str, expected: str) -> DiscoveredCamera:
    if source != expected:
        raise ProtocolError("Discovery response from unexpected host")
    kind, body = parse_datagram(data)
    if kind != 200:
        raise ProtocolError("Unexpected discovery packet type")
    for unit in parse_units(body):
        if unit.get("A") != 8 or unit.get("V") != 2:
            continue
        if "C" in unit and (type(unit["C"]) is not int or unit["C"] != 0):
            raise ProtocolError("Discovery response reports failure")
        params = unit.get("P")
        if not isinstance(params, list) or len(params) < 5:
            raise ProtocolError("Invalid discovery parameters")
        host, port, key = params[2:5]
        if host != expected or type(port) is not int or not 1 <= port <= 65535:
            raise ProtocolError("Discovery endpoint does not match target")
        if not isinstance(key, str) or not key:
            raise ProtocolError("Discovery response has no local key")
        return DiscoveredCamera(host, port, key)
    raise ProtocolError("No V2 discovery response")


def discover(source: str, target: str, timeout: float = 2.0) -> DiscoveredCamera:
    ipaddress.IPv4Address(source)
    ipaddress.IPv4Address(target)
    request = command_packet([{"Q": 8, "V": 2, "P": []}])
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((source, 0))
        sock.sendto(request, (target, 36868))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Discovery timed out")
            sock.settimeout(remaining)
            data, (host, _) = sock.recvfrom(65535)
            if host == target:
                return parse_discovery(data, host, target)
