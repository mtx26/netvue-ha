import socket
from contextlib import contextmanager
from typing import Iterator

from .discovery import DiscoveredCamera, validate_lan_ipv4
from .protocol import ProtocolError


@contextmanager
def connect(camera: DiscoveredCamera, timeout: float = 2.0) -> Iterator[socket.socket]:
    validate_lan_ipv4(camera.host)
    if camera.port != 5555:
        raise ProtocolError("Camera TCP port is not authorized")
    with socket.create_connection((camera.host, camera.port), timeout=timeout) as sock:
        yield sock
