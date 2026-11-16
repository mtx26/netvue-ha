import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from netvue_bridge.budget import Budget
from netvue_bridge.manager import CameraManager


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.directory.name)
        self.manager = CameraManager("192.168.10.1", self.state_dir, control_port=0)
        await self.manager.start()
        self.port = self.manager._server.sockets[0].getsockname()[1]
        self.processes = []

    async def asyncTearDown(self) -> None:
        await self.manager.close()
        self.directory.cleanup()

    async def request(self, method: str, path: str, data: dict | None = None):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        body = json.dumps(data).encode() if data is not None else b""
        request = (
            f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n"
            f"Content-Length: {len(body)}\r\n"
        )
        if body:
            request += "Content-Type: application/json\r\n"
        writer.write(request.encode() + b"\r\n" + body)
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        head, content = response.split(b"\r\n\r\n", 1)
        self.response_line = head.split(b"\r\n", 1)[0]
        status = int(self.response_line.split()[1])
        return status, json.loads(content)

    async def fake_spawn(self, *args, **kwargs):
        process = FakeProcess(1000 + len(self.processes))
        self.processes.append((process, args))
        return process

    async def test_sessions_are_unique_isolated_and_removable(self):
        with patch("netvue_bridge.manager.asyncio.create_subprocess_exec",
                   side_effect=self.fake_spawn):
            status, body = await self.request(
                "POST", "/api/v1/cameras",
                {"host": "192.168.10.2", "stream_name": "netvue_192_168_10_2"},
            )
            self.assertEqual(status, 202)
            self.assertEqual(body["state"], "starting")
            self.assertIn("rtsp://127.0.0.1:8554/netvue_192_168_10_2",
                          self.processes[0][1])

            status, body = await self.request(
                "POST", "/api/v1/cameras",
                {"host": "192.168.10.2", "stream_name": "netvue_192_168_10_2"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(len(self.processes), 1)

            status, _ = await self.request(
                "POST", "/api/v1/cameras",
                {"host": "192.168.10.3", "stream_name": "netvue_192_168_10_3"},
            )
            self.assertEqual(status, 202)
            status, _ = await self.request(
                "POST", "/api/v1/cameras",
                {"host": "192.168.10.4", "stream_name": "netvue_192_168_10_4"},
            )
            self.assertEqual(status, 202)
            self.assertEqual(
                len({camera.state_dir for camera in self.manager._cameras.values()}), 3
            )
            status, body = await self.request("GET", "/api/v1/status")
            self.assertEqual(status, 200)
            self.assertEqual(len(body["cameras"]), 3)

            status, _ = await self.request("DELETE", "/api/v1/cameras/192.168.10.2")
            self.assertEqual(status, 200)
            self.assertTrue(self.processes[0][0].terminated)

    async def test_invalid_input_and_blocked_budget_do_not_start_sessions(self):
        with patch("netvue_bridge.manager.asyncio.create_subprocess_exec",
                   side_effect=self.fake_spawn):
            status, _ = await self.request(
                "POST", "/api/v1/cameras", {"host": 3232238082, "stream_name": "bad"}
            )
            self.assertEqual(status, 400)
            self.assertEqual(self.processes, [])

        with Budget(self.state_dir, "192.168.10.1", "192.168.10.2") as budget:
            budget.initialize()
            budget.reserve()
            budget.block()
        await self.manager.close()
        self.manager = CameraManager("192.168.10.1", self.state_dir, control_port=0)
        await self.manager.start()
        self.port = self.manager._server.sockets[0].getsockname()[1]
        with patch("netvue_bridge.manager.asyncio.create_subprocess_exec",
                   side_effect=self.fake_spawn):
            status, body = await self.request(
                "POST", "/api/v1/cameras",
                {"host": "192.168.10.2", "stream_name": "netvue_192_168_10_2"},
            )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "Camera budget is blocked")
        self.assertEqual(self.processes, [])

    async def test_restart_waits_for_previous_session_to_stop(self):
        with patch("netvue_bridge.manager.asyncio.create_subprocess_exec",
                   side_effect=self.fake_spawn):
            status, _ = await self.request(
                "POST", "/api/v1/cameras",
                {"host": "192.168.10.2", "stream_name": "netvue_192_168_10_2"},
            )
            self.assertEqual(status, 202)
            stopping = asyncio.Event()
            release_stop = asyncio.Event()
            stop_process = self.manager._stop_process

            async def delayed_stop(camera):
                stopping.set()
                await release_stop.wait()
                await stop_process(camera)

            with patch.object(self.manager, "_stop_process", side_effect=delayed_stop):
                removal = asyncio.create_task(
                    self.request("DELETE", "/api/v1/cameras/192.168.10.2")
                )
                await asyncio.wait_for(stopping.wait(), 1)
                restart = asyncio.create_task(
                    self.request(
                        "POST", "/api/v1/cameras",
                        {"host": "192.168.10.2", "stream_name": "netvue_192_168_10_2"},
                    )
                )
                await asyncio.sleep(0)
                self.assertFalse(restart.done())
                release_stop.set()
                removed, started = await asyncio.gather(removal, restart)

        self.assertEqual(removed[0], 200)
        self.assertEqual(started[0], 202)
        self.assertEqual(len(self.processes), 2)

    async def test_ambiguous_existing_root_state_fails_closed(self):
        (self.state_dir / "budget.json").write_text("{invalid", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not version 4"):
            CameraManager("192.168.10.1", self.state_dir, control_port=0)

    async def test_http_status_uses_standard_reason_phrase(self):
        status, _ = await self.request("GET", "/missing")
        self.assertEqual(status, 404)
        self.assertEqual(self.response_line, b"HTTP/1.1 404 Not Found")


if __name__ == "__main__":
    unittest.main()
