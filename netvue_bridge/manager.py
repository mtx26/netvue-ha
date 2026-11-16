"""Loopback control API for Home Assistant-managed camera sessions."""

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import signal
import stat
import sys
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlsplit

from .budget import Budget
from .discovery import validate_lan_ipv4
from .rtsp import validate_local_rtsp_url

CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 8765
MAX_REQUEST_SIZE = 8192
MAX_BODY_SIZE = 4096
STREAM_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


@dataclass
class ManagedCamera:
    host: str
    stream_name: str
    state_dir: Path
    process: asyncio.subprocess.Process


class RequestError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message


class CameraManager:
    def __init__(self, source: str, state_dir: Path, control_port: int = CONTROL_PORT) -> None:
        self.source = validate_lan_ipv4(source)
        self.state_dir = state_dir
        if type(control_port) is not int or not 0 <= control_port <= 65535:
            raise ValueError("Invalid control API port")
        self.control_port = control_port
        if not state_dir.is_dir():
            raise ValueError("State directory must already exist")
        self._primary_target = self._read_primary_budget()
        self._cameras: dict[str, ManagedCamera] = {}
        self._lock = asyncio.Lock()
        self._server: asyncio.Server | None = None

    def _read_primary_budget(self) -> str | None:
        budget_path = self.state_dir / "budget.json"
        try:
            fd = os.open(budget_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise ValueError("Existing budget state must be a regular file")
            raw = file.read(4097)
        if len(raw) > 4096:
            raise ValueError("Existing budget file is too large")
        try:
            state = json.loads(raw)
            target = validate_lan_ipv4(state["target"])
            with Budget(self.state_dir, self.source, target) as budget:
                budget.read()
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise ValueError(
                "Existing root budget is invalid or not version 4"
            ) from exc
        return target

    def _camera_state_dir(self, target: str) -> Path:
        if target == self._primary_target:
            return self.state_dir

        name = hashlib.sha256(target.encode("ascii")).hexdigest()[:24]
        path = self.state_dir / f"camera-{name}"
        created = False
        try:
            path.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass

        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Camera state path must be a real directory")
        budget_path = path / "budget.json"
        if created:
            try:
                with Budget(path, self.source, target) as budget:
                    budget.initialize()
            except FileExistsError as exc:
                raise ValueError("Camera budget already exists") from exc
        if not budget_path.is_file():
            raise ValueError("Camera state is incomplete")
        return path

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("Manager is already running")
        self._server = await asyncio.start_server(
            self._handle_connection, CONTROL_HOST, self.control_port, limit=MAX_REQUEST_SIZE
        )

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        async with self._lock:
            cameras = list(self._cameras.values())
            self._cameras.clear()
        await asyncio.gather(*(self._stop_process(camera) for camera in cameras))

    async def _stop_process(self, camera: ManagedCamera) -> None:
        process = camera.process
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=12)
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()

    async def _ensure_camera(self, host: str, stream_name: str) -> tuple[int, str]:
        if not isinstance(host, str) or not isinstance(stream_name, str):
            raise RequestError(400, "Camera address and stream name must be strings")
        target = validate_lan_ipv4(host)
        if target == self.source or not STREAM_NAME.fullmatch(stream_name):
            raise RequestError(400, "Invalid camera address or stream name")
        url = f"rtsp://127.0.0.1:8554/{stream_name}"
        try:
            validate_local_rtsp_url(url)
        except ValueError as exc:
            raise RequestError(400, "Invalid RTSP stream name") from exc

        async with self._lock:
            existing = self._cameras.get(target)
            if existing is not None:
                if existing.stream_name != stream_name:
                    raise RequestError(409, "Camera already has another stream assignment")
                if existing.process.returncode is not None:
                    raise RequestError(409, "Camera supervisor stopped; inspect its budget state")
                return existing.process.pid, "running"
            if any(camera.stream_name == stream_name for camera in self._cameras.values()):
                raise RequestError(409, "RTSP stream name is already assigned")

            try:
                state_dir = self._camera_state_dir(target)
                with Budget(state_dir, self.source, target) as budget:
                    budget_state = budget.read()
            except (OSError, RuntimeError, ValueError) as exc:
                raise RequestError(409, "Camera budget is unavailable or ambiguous") from exc
            if budget_state["status"] == "blocked":
                raise RequestError(409, "Camera budget is blocked")
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-B", "-m", "netvue_bridge.continuous", "run",
                "--source", self.source, "--target", target, "--state-dir", str(state_dir),
                "--rtsp", url,
            )
            if process.returncode is not None:
                raise RequestError(503, "Camera supervisor failed to start")
            self._cameras[target] = ManagedCamera(target, stream_name, state_dir, process)
            return process.pid, "starting"

    async def _remove_camera(self, target: str) -> bool:
        async with self._lock:
            camera = self._cameras.get(target)
            if camera is None:
                return False
            await self._stop_process(camera)
            self._cameras.pop(target)
            return True

    def _status(self) -> list[dict[str, str]]:
        return [
            {"host": camera.host, "stream_name": camera.stream_name,
             "state": "running" if camera.process.returncode is None else "stopped"}
            for camera in self._cameras.values()
        ]

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        status = 400
        body: dict[str, object] = {"error": "Invalid request"}
        peer = writer.get_extra_info("peername")
        try:
            if not peer or not ipaddress.ip_address(peer[0]).is_loopback:
                raise RequestError(403, "Loopback access only")
            method, path, payload = await self._read_request(reader)
            if method == "GET" and path == "/api/v1/status":
                status, body = 200, {"cameras": self._status()}
            elif method == "POST" and path == "/api/v1/cameras":
                if not isinstance(payload, dict) or set(payload) != {"host", "stream_name"}:
                    raise RequestError(400, "Expected camera address and stream name")
                pid, state = await self._ensure_camera(payload["host"], payload["stream_name"])
                status = 200 if state == "running" else 202
                body = {"pid": pid, "state": state}
            elif method == "DELETE" and path.startswith("/api/v1/cameras/"):
                target = path.removeprefix("/api/v1/cameras/")
                target = validate_lan_ipv4(target)
                status = 200 if await self._remove_camera(target) else 404
                body = {"state": "stopped" if status == 200 else "not_found"}
            else:
                status, body = 404, {"error": "Not found"}
        except RequestError as exc:
            status, body = exc.status, {"error": exc.message}
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, ValueError):
            status, body = 400, {"error": "Invalid request"}
        except (OSError, RuntimeError):
            status, body = 503, {"error": "Bridge manager unavailable"}
        try:
            encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
            header = (
                f"HTTP/1.1 {status} {HTTPStatus(status).phrase}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(encoded)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            writer.write(header + encoded)
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _read_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, dict[str, object] | None]:
        total = 0

        async def line() -> bytes:
            nonlocal total
            value = await asyncio.wait_for(reader.readline(), timeout=3)
            total += len(value)
            if not value or total > MAX_REQUEST_SIZE or not value.endswith(b"\r\n"):
                raise ValueError("Invalid request headers")
            return value

        request = (await line())[:-2].decode("ascii").split(" ")
        if len(request) != 3 or request[2] != "HTTP/1.1":
            raise ValueError("Invalid request line")
        method, path, _ = request
        headers: dict[str, str] = {}
        while True:
            value = await line()
            if value == b"\r\n":
                break
            name, separator, content = value[:-2].partition(b":")
            if not separator:
                raise ValueError("Invalid header")
            key = name.decode("ascii").lower()
            if key in headers:
                raise ValueError("Duplicate header")
            headers[key] = content.decode("ascii").strip()
        if "transfer-encoding" in headers:
            raise ValueError("Transfer encoding is not supported")
        length = int(headers.get("content-length", "0"))
        if not 0 <= length <= MAX_BODY_SIZE:
            raise ValueError("Invalid body length")
        if method == "POST" and headers.get("content-type", "").split(";", 1)[0] != "application/json":
            raise ValueError("JSON body required")
        raw = await asyncio.wait_for(reader.readexactly(length), timeout=3) if length else b""
        payload = json.loads(raw) if raw else None
        return method, path, payload


async def serve(source: str, state_dir: Path) -> None:
    manager = CameraManager(source, state_dir)
    await manager.start()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopped.set)
    try:
        await stopped.wait()
    finally:
        await manager.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Home Assistant Netvue session manager")
    parser.add_argument("--source", required=True)
    parser.add_argument("--state-dir", type=Path, default=Path("/state"))
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.source, args.state_dir))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"manager: error={type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
