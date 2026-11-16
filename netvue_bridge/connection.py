import socket
from contextlib import contextmanager
from typing import Iterator

from .discovery import DiscoveredCamera


@contextmanager
def connect(camera: DiscoveredCamera, timeout: float = 2.0) -> Iterator[socket.socket]:
    with socket.create_connection((camera.host, camera.port), timeout=timeout) as sock:
        yield sock
