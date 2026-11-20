import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harness import HassTestCase


class HarnessCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_waits_for_cancelled_executor_work(self):
        tmp = tempfile.TemporaryDirectory(prefix="netvue-ha-cleanup-test-")
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        wrote_in_existing_directory = []

        def write_storage():
            started.set()
            release.wait()
            directory = Path(tmp.name)
            wrote_in_existing_directory.append(directory.is_dir())
            if directory.is_dir():
                (directory / "storage").write_text("data")
            finished.set()

        loop = asyncio.get_running_loop()
        work = loop.run_in_executor(None, write_storage)
        await asyncio.to_thread(started.wait)
        work.cancel()
        harness = SimpleNamespace(
            hass=SimpleNamespace(async_stop=AsyncMock()),
            bridge=SimpleNamespace(stop=AsyncMock()),
            _tmp=tmp,
        )
        cleanup = asyncio.create_task(HassTestCase._cleanup(harness))
        try:
            await asyncio.sleep(0)
            self.assertFalse(cleanup.done())
        finally:
            release.set()
            await cleanup
        self.assertTrue(finished.is_set())
        self.assertEqual(wrote_in_existing_directory, [True])
        self.assertFalse(Path(tmp.name).exists())
