import os
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if os.name == "posix":
    from netvue_bridge.continuous import (
        DAY, WEEK, RollingBudget, clock_sample, reconnect_delay, run_worker, supervise_continuous,
    )
    from netvue_bridge.protocol import command_packet
    from netvue_bridge.stream import StreamInterrupted, receive_stream


@unittest.skipUnless(os.name == "posix", "requires Linux file locks")
class ContinuousTests(unittest.TestCase):
    source = "192.0.2.10"
    target = "192.0.2.20"
    rtsp = "rtsp://127.0.0.1:8554/netvue2"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.wall, self.boot, self.boot_id = 1700000000.0, 1000000.0, "test-boot"
        clock = patch("netvue_bridge.continuous.clock_sample",
                      side_effect=lambda: (self.wall, self.boot, self.boot_id))
        clock.start()
        self.addCleanup(clock.stop)

    def advance(self, seconds):
        self.wall += seconds
        self.boot += seconds

    def test_three_in_rolling_day_and_latched_exhaustion(self):
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            for attempt in range(3):
                self.assertEqual(budget.reserve(), attempt + 1)
                budget.finish_retryable("live")
                self.advance(600)
            with self.assertRaisesRegex(ValueError, "BUDGET_EXHAUSTED"):
                budget.reserve()
            self.assertEqual(budget.read()["status"], "blocked")
            self.advance(DAY)
            with self.assertRaisesRegex(ValueError, "BUDGET_BLOCKED"):
                budget.reserve()
        with RollingBudget(self.path, self.source, self.target) as budget:
            self.assertEqual(budget.read()["total"], 3)
            self.assertEqual(budget.read()["status"], "blocked")

    def test_rolling_week_and_exact_day_boundary(self):
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            for attempt in range(8):
                budget.reserve()
                budget.finish_retryable("live")
                self.advance(20 * 3600 if attempt < 7 else 1801)
            with self.assertRaisesRegex(ValueError, "BUDGET_EXHAUSTED"):
                budget.reserve()
            self.assertEqual(budget.read()["total"], 8)

    def test_older_than_seven_days_expires_without_resetting_total(self):
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
            budget.finish_retryable("live")
            self.advance(WEEK)
            self.assertEqual(budget.reserve(), 2)
            self.assertEqual(len(budget.read()["attempts"]), 1)
            self.assertEqual(budget.read()["last_reconnect"], self.wall)

    def test_concurrent_lock_crash_and_restart_fail_closed(self):
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            with self.assertRaises(BlockingIOError):
                with RollingBudget(self.path, self.source, self.target):
                    pass
            budget.reserve()
            budget.stage("AUTHENTICATING")
        with RollingBudget(self.path, self.source, self.target) as budget, \
             patch("netvue_bridge.continuous.wait_for_rtsp") as network:
            self.assertEqual(budget.read()["stage"], "AUTHENTICATING")
            self.assertEqual(supervise_continuous(budget, self.rtsp), 1)
            network.assert_not_called()

    def test_synthetic_attested_interruption_preserves_history_and_quota(self):
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
            budget.stage("STREAMING")
            original = budget.read()
            # This models a separately verified clean stop, not an ambiguous crash.
            with patch("netvue_bridge.continuous.reconnect_delay", return_value=30):
                budget.finish_retryable("live")
            state = budget.read()
            self.assertEqual(state["attempts"], original["attempts"])
            self.assertEqual(state["total"], 1)
            self.assertEqual((state["status"], state["stage"]), ("ready", "RECONNECT_WAIT"))
            with self.assertRaisesRegex(ValueError, "backoff"):
                budget.reserve()
            self.advance(30)
            budget.reserve()
            budget.finish_retryable("live")
            self.advance(1801)
            budget.reserve()
            budget.finish_retryable("live")
            self.advance(1801)
            with self.assertRaisesRegex(ValueError, "BUDGET_EXHAUSTED"):
                budget.reserve()
            self.assertEqual(budget.read()["total"], 3)

    def test_blocked_budget_has_no_existing_reconciliation(self):
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
            budget.block()
            with self.assertRaisesRegex(ValueError, "No attempt in progress"):
                budget.finish_retryable("live")
            with self.assertRaisesRegex(ValueError, "BUDGET_BLOCKED"):
                budget.reserve()
            self.assertEqual(budget.read()["total"], 1)

    def test_clock_change_and_reboot_block_before_network(self):
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
            budget.finish_retryable("live")
        for kind in ("clock", "boot"):
            with self.subTest(kind=kind), RollingBudget(self.path, self.source, self.target) as budget:
                old_wall, old_id = self.wall, self.boot_id
                if kind == "clock":
                    self.wall += DAY
                else:
                    self.boot_id = "other-boot"
                with patch("netvue_bridge.continuous.wait_for_rtsp") as network:
                    with self.assertRaisesRegex(ValueError, "CLOCK_AMBIGUOUS"):
                        supervise_continuous(budget, self.rtsp)
                    network.assert_not_called()
                self.wall, self.boot_id = old_wall, old_id

    def test_backoff_persists_and_retry_is_reserved_before_worker(self):
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
            with patch("netvue_bridge.continuous.reconnect_delay", return_value=30):
                budget.finish_retryable("live")
        with RollingBudget(self.path, self.source, self.target) as budget:
            with self.assertRaisesRegex(ValueError, "backoff"):
                budget.reserve()
            self.advance(30)
            with patch("netvue_bridge.continuous.wait_for_rtsp", return_value=True), \
                 patch("netvue_bridge.continuous.run_worker", return_value="blocked") as run:
                self.assertEqual(supervise_continuous(budget, self.rtsp), 1)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(budget.read()["total"], 2)
                self.assertEqual(budget.read()["status"], "blocked")

    def test_backoff_minima_and_cap(self):
        for count, minimum in ((1, 30), (2, 120), (3, 300), (4, 600), (10, 1800)):
            for _ in range(20):
                self.assertGreaterEqual(reconnect_delay(count), minimum)
                self.assertLessEqual(reconnect_delay(count), min(1800, minimum * 1.2))

    def test_unavailable_rtsp_and_bad_budget_never_connect(self):
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            with patch("netvue_bridge.continuous.wait_for_rtsp", return_value=False), \
                 patch("netvue_bridge.continuous.run_worker") as worker:
                self.assertEqual(supervise_continuous(budget, self.rtsp), 1)
                worker.assert_not_called()
                self.assertEqual(budget.read()["total"], 0)
        (self.path / "budget.json").write_text("{}", encoding="utf-8")
        with RollingBudget(self.path, self.source, self.target) as budget, \
             patch("netvue_bridge.continuous.wait_for_rtsp") as wait:
            with self.assertRaises(ValueError):
                supervise_continuous(budget, self.rtsp)
            wait.assert_not_called()

    def test_inconsistent_ready_state_cannot_reconnect(self):
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
            state = budget.read()
            state["status"] = "ready"
            budget._write(state)
            with patch("netvue_bridge.continuous.wait_for_rtsp") as network:
                with self.assertRaisesRegex(ValueError, "invalid"):
                    supervise_continuous(budget, self.rtsp)
                network.assert_not_called()

    def test_video_without_reader_and_then_video_stall(self):
        publisher = MagicMock()
        publisher.published_frames = 0

        def push(data, timestamp):
            publisher.published_frames += 1

        publisher.push.side_effect = push
        left, right = socket.socketpair()
        try:
            right.sendall(command_packet([{"A": 3, "V": 2, "C": 0}]))
            body = b"\x10" + (1).to_bytes(4, "little") + b"fake"
            right.sendall(struct.pack("<IIIHH", len(body), 0, 0, 0, 201) + body)
            received = []
            started = time.monotonic()
            with self.assertRaises(StreamInterrupted):
                receive_stream(left, publisher, 8, received.append)
            self.assertEqual(len(received), 1)
            self.assertEqual(publisher.published_frames, 1)
            self.assertGreaterEqual(time.monotonic() - started, 2.9)
        finally:
            left.close()
            right.close()

    def test_worker_crash_and_unresponsive_worker_remain_ambiguous(self):
        original_popen = subprocess.Popen
        children = []
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
            for script in ("raise SystemExit(1)", "import time; time.sleep(20)"):
                def launch(command, **kwargs):
                    child = original_popen([sys.executable, "-c", script], **kwargs)
                    children.append(child)
                    return child

                with patch("netvue_bridge.continuous.subprocess.Popen", side_effect=launch), \
                     patch("netvue_bridge.continuous.HEARTBEAT_TIMEOUT", 0.1), \
                     patch("netvue_bridge.continuous.resource_snapshot",
                           return_value={"memory_bytes": 1, "memory_limit_bytes": 100,
                                         "cpu_usec": 1}):
                    self.assertEqual(run_worker(budget, self.rtsp), "ambiguous")
                self.assertIsNotNone(children[-1].poll())
                self.assertEqual(budget.read()["status"], "in_progress")

    def test_worker_explicit_refusal_and_memory_limit(self):
        original_popen = subprocess.Popen
        with RollingBudget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()

            def refusal(command, **kwargs):
                fd = command[command.index("--event-fd") + 1]
                script = "import os,sys; os.write(int(sys.argv[1]), b'{\"event\":\"blocked\"}\\n'); sys.exit(1)"
                return original_popen([sys.executable, "-c", script, fd], **kwargs)

            with patch("netvue_bridge.continuous.subprocess.Popen", side_effect=refusal), \
                 patch("netvue_bridge.continuous.resource_snapshot",
                       return_value={"memory_bytes": 1, "memory_limit_bytes": 100, "cpu_usec": 1}):
                self.assertEqual(run_worker(budget, self.rtsp), "blocked")

            def stalled(command, **kwargs):
                return original_popen([sys.executable, "-c", "import time; time.sleep(20)"], **kwargs)

            with patch("netvue_bridge.continuous.subprocess.Popen", side_effect=stalled), \
                 patch("netvue_bridge.continuous.resource_snapshot",
                       return_value={"memory_bytes": 95, "memory_limit_bytes": 100, "cpu_usec": 1}):
                self.assertEqual(run_worker(budget, self.rtsp), "ambiguous")
            self.assertEqual(budget.read()["status"], "in_progress")

    def test_sigterm_stops_child_and_keeps_attempt_in_progress(self):
        target = f"127.254.{(os.getpid() >> 8) % 256}.{os.getpid() % 256}"
        with patch("netvue_bridge.continuous.clock_sample", new=clock_sample), \
             socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as camera, \
             socket.socket() as rtsp:
            camera.bind((target, 36868))
            camera.settimeout(8)
            rtsp.bind(("127.0.0.1", 0))
            rtsp.listen(1)
            with RollingBudget(self.path, "127.0.0.1", target) as budget:
                budget.initialize()
            process = subprocess.Popen(
                [sys.executable, "-B", "-m", "netvue_bridge.continuous", "run",
                 "--source", "127.0.0.1", "--target", target,
                 "--state-dir", str(self.path),
                 "--rtsp", f"rtsp://127.0.0.1:{rtsp.getsockname()[1]}/netvue2"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                camera.recvfrom(65535)
                children = Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()
                self.assertEqual(len(children), 1)
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 130, stderr)
                self.assertIn("STOPPED", stdout)
                self.assertFalse(Path("/proc").joinpath(children[0]).exists())
                with RollingBudget(self.path, "127.0.0.1", target) as budget:
                    self.assertEqual(budget.read()["status"], "in_progress")
                    self.assertEqual(budget.read()["total"], 1)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=8)


if __name__ == "__main__":
    unittest.main()
