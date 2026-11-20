"""Regression tests for the isolated Home Assistant E2E harness."""

import asyncio
import subprocess
import sys
import tempfile
import unittest

from ha_go_e2e_synthetic import check_bridge_race_report, stop_process


class BridgeRaceReportTests(unittest.TestCase):
    def test_child_race_report_fails_validation(self):
        for output, has_race in (("normal child log", False), ("WARNING: DATA RACE", True)):
            with self.subTest(output=output), tempfile.TemporaryFile() as logs:
                subprocess.run(
                    [sys.executable, "-c", "import sys; print(sys.argv[1], file=sys.stderr)", output],
                    stderr=logs,
                    check=True,
                )
                if has_race:
                    with self.assertRaisesRegex(RuntimeError, "DATA RACE"):
                        check_bridge_race_report(logs)
                else:
                    check_bridge_race_report(logs)


class ChildCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_child_exit_fails_validation(self):
        process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"])
        process.wait(timeout=10)
        with self.assertRaisesRegex(RuntimeError, "exited unexpectedly: 7"):
            await stop_process(process, "bridge")

    async def test_running_child_is_stopped(self):
        with subprocess.Popen(
            [sys.executable, "-u", "-c", "print('ready'); input()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        ) as process:
            assert process.stdout is not None
            ready = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 10)
            self.assertEqual(ready.strip(), b"ready")
            await stop_process(process, "bridge")
            self.assertIsNotNone(process.poll())
