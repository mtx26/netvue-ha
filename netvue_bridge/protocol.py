import json
import socket
import struct
import time
from typing import Any


HEADER = struct.Struct("<IIIHH")
MAX_BODY = 1024 * 1024


class ProtocolError(Exception):
    pass


class ConnectionClosed(ProtocolError):
    pass


def command_packet(units: list[dict[str, Any]]) -> bytes:
    body = json.dumps(units, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if not body or len(body) >= MAX_BODY:
        raise ProtocolError("Invalid command size")
    return HEADER.pack(len(body), 0, 0, 0, 200) + body


def _validate_header(data: bytes, body_size: int | None = None) -> tuple[int, int]:
    length, _, _, flag, kind = HEADER.unpack_from(data)
    if length <= 0 or length >= MAX_BODY or (body_size is not None and length != body_size):
        raise ProtocolError("Invalid packet size")
    if flag != 0:
        raise ProtocolError("Encrypted or flagged packet unsupported")
    return length, kind


def parse_datagram(data: bytes) -> tuple[int, bytes]:
    if len(data) < HEADER.size:
        raise ProtocolError("Truncated header")
    _, kind = _validate_header(data, len(data) - HEADER.size)
    return kind, data[HEADER.size:]


def parse_units(body: bytes) -> list[dict[str, Any]]:
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Invalid command JSON") from exc
    if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
        raise ProtocolError("Invalid command list")
    return decoded


def read_exact(sock: socket.socket, size: int, deadline: float) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Packet deadline exceeded")
        sock.settimeout(remaining)
        piece = sock.recv(size - len(chunks))
        if not piece:
            raise ConnectionClosed("Connection closed while reading")
        chunks.extend(piece)
    return bytes(chunks)


def read_packet(sock: socket.socket, deadline: float) -> tuple[int, bytes]:
    header = read_exact(sock, HEADER.size, deadline)
    length, kind = _validate_header(header)
    return kind, read_exact(sock, length, deadline)
