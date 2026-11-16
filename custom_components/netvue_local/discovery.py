"""Explicit, one-datagram LAN discovery; no TCP or authentication."""

import asyncio
import ipaddress
import json
import logging
import struct
from dataclasses import dataclass

from .const import DISCOVERY_PORT, DISCOVERY_TIMEOUT

_LOGGER = logging.getLogger(__name__)
_HEADER = struct.Struct("<IIIHH")
_MAX_PACKET = 65507
_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                          ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


@dataclass(frozen=True)
class Candidate:
    host: str


def local_network(address: str, prefix: int) -> ipaddress.IPv4Network:
    interface = ipaddress.IPv4Interface(f"{address}/{prefix}")
    if (not 23 <= prefix <= 30
            or not any(interface.network.subnet_of(network) for network in _PRIVATE_NETWORKS)
            or interface.ip in (interface.network.network_address, interface.network.broadcast_address)):
        raise ValueError("Discovery requires an RFC1918 subnet between /23 and /30")
    return interface.network


def parse_response(data: bytes, sender: str, network: ipaddress.IPv4Network) -> Candidate | None:
    try:
        address = ipaddress.IPv4Address(sender)
    except ipaddress.AddressValueError:
        return None
    if (address not in network or address in (network.network_address, network.broadcast_address)
            or len(data) < _HEADER.size or len(data) > _MAX_PACKET):
        return None
    length, _, _, flag, kind = _HEADER.unpack_from(data)
    if not length or length != len(data) - _HEADER.size or flag or kind != 200:
        return None
    try:
        units = json.loads(data[_HEADER.size:].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(units, list) or not all(isinstance(unit, dict) for unit in units):
        return None
    for unit in units:
        if unit.get("A") != 8 or unit.get("V") != 2:
            continue
        if "C" in unit and (type(unit["C"]) is not int or unit["C"] != 0):
            continue
        params = unit.get("P")
        if (not isinstance(params, list) or len(params) < 5
                or params[2] != sender or type(params[3]) is not int
                or not 1 <= params[3] <= 65535
                or not isinstance(params[4], str) or not params[4]):
            continue
        return Candidate(host=sender)
    return None


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, source: str, network: ipaddress.IPv4Network) -> None:
        self.source = source
        self.network = network
        self.candidates: dict[str, Candidate] = {}
        self.invalid = 0
        self.error: Exception | None = None

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        candidate = parse_response(data, addr[0], self.network)
        if candidate is None or candidate.host == self.source:
            self.invalid += 1
        elif len(self.candidates) < 128:
            self.candidates[candidate.host] = candidate

    def error_received(self, exc: Exception) -> None:
        self.error = exc


async def async_discover(source: str, prefix: int) -> list[Candidate]:
    network = local_network(source, prefix)
    protocol = _DiscoveryProtocol(source, network)
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol, local_addr=(source, 0), allow_broadcast=True
    )
    try:
        body = json.dumps([{"Q": 8, "V": 2, "P": []}], separators=(",", ":")).encode()
        transport.sendto(_HEADER.pack(len(body), 0, 0, 0, 200) + body,
                         (str(network.broadcast_address), DISCOVERY_PORT))
        await asyncio.sleep(DISCOVERY_TIMEOUT)
        if protocol.error is not None:
            raise OSError("UDP discovery failed") from protocol.error
        if protocol.invalid:
            _LOGGER.debug("Ignored %d invalid discovery replies", protocol.invalid)
        return sorted(protocol.candidates.values(), key=lambda candidate: int(
            ipaddress.IPv4Address(candidate.host)))
    finally:
        transport.close()
