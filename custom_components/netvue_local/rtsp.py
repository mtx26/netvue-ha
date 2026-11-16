"""Validate and probe only a pre-existing, loopback RTSP publisher."""

import asyncio
import ipaddress
import re
from urllib.parse import urlsplit

_PATH = re.compile(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\Z")
_STREAM_NAME = re.compile(r"[A-Za-z0-9_-]+\Z")


def stream_url_from_name(name: str) -> str:
    if not _STREAM_NAME.fullmatch(name):
        raise ValueError("Invalid RTSP stream name")
    return f"rtsp://127.0.0.1:8554/{name}"


def stream_name_for_host(host: str) -> str:
    address = validate_host(host)
    return "netvue_" + address.replace(".", "_")


def stream_name_from_url(url: str) -> str | None:
    validate_stream_url(url)
    parts = urlsplit(url)
    name = parts.path[1:]
    if _STREAM_NAME.fullmatch(name) and stream_url_from_name(name) == url:
        return name
    return None


def validate_host(value: str) -> str:
    address = ipaddress.IPv4Address(value)
    networks = tuple(ipaddress.ip_network(item) for item in
                     ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    if not any(address in network for network in networks):
        raise ValueError("Camera address must be RFC1918 IPv4")
    return str(address)


def validate_stream_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ValueError("Invalid RTSP URL") from exc
    if (parts.scheme != "rtsp" or parts.netloc != f"127.0.0.1:{port}"
            or port is None or not 1 <= port <= 65535
            or not _PATH.fullmatch(parts.path) or parts.query or parts.fragment):
        raise ValueError("RTSP must be loopback with an explicit port and a simple path")
    return value


async def async_probe_stream(url: str) -> bool:
    validate_stream_url(url)
    port = urlsplit(url).port
    assert port is not None
    writer = None
    try:
        async with asyncio.timeout(4):
            reader, writer = await asyncio.open_connection("127.0.0.1", port, limit=16384)
            writer.write(f"DESCRIBE {url} RTSP/1.0\r\nCSeq: 1\r\n"
                         "Accept: application/sdp\r\n\r\n".encode("ascii"))
            await writer.drain()
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.split(b"\r\n")
            if not re.fullmatch(rb"RTSP/1\.[01] 200(?: .*)?", lines[0]):
                return False
            headers: dict[bytes, bytes] = {}
            for line in lines[1:]:
                if b":" in line:
                    name, value = line.split(b":", 1)
                    headers[name.strip().lower()] = value.strip()
            if b"application/sdp" not in headers.get(b"content-type", b"").lower():
                return False
            length = int(headers.get(b"content-length", b"0"))
            if not 0 < length <= 16384:
                return False
            sdp = await reader.readexactly(length)
            return bool(re.search(rb"(?im)^a=rtpmap:\d+\s+H264/\d+", sdp))
    except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError,
            asyncio.LimitOverrunError):
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
