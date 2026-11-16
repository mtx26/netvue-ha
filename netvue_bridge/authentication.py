import secrets
import socket
import time

from .discovery import DiscoveredCamera
from .protocol import ProtocolError, command_packet, parse_units, read_packet


def login_packet(camera: DiscoveredCamera) -> bytes:
    nonce = secrets.randbits(32) - (1 << 31)
    return command_packet([{"Q": 1, "V": 1, "P": [
        camera.key, nonce, "localUser", camera.host, camera.port
    ]}])


def authenticate(sock: socket.socket, camera: DiscoveredCamera, timeout: float = 4.0) -> int:
    sock.settimeout(timeout)
    sock.sendall(login_packet(camera))
    deadline = time.monotonic() + timeout
    while True:
        kind, body = read_packet(sock, deadline)
        if kind != 200:
            raise ProtocolError("Unexpected packet during authentication")
        for unit in parse_units(body):
            if unit.get("A") != 1:
                continue
            if unit.get("V") not in (1, 2):
                raise ProtocolError("Unsupported authentication response version")
            code = unit.get("C")
            if type(code) is not int:
                raise ProtocolError("Authentication response has no explicit result code")
            return code
