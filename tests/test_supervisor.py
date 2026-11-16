import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

if os.name == "posix":
    from netvue_bridge.supervisor import Budget, retry_delay, run_bounded, supervise


@unittest.skipUnless(os.name == "posix", "supervisor requires Linux file locks")
class SupervisorTests(unittest.TestCase):
    source = "192.0.2.10"
    target = "192.0.2.20"
    rtsp = "rtsp://127.0.0.1:8554/netvue2"

    def test_budget_is_persistent_exclusive_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with Budget(path, self.source, self.target) as budget:
                with self.assertRaises(FileNotFoundError):
                    budget.read()
                budget.initialize(2)
                with self.assertRaises(FileExistsError):
                    budget.initialize(2)
                with self.assertRaises(BlockingIOError):
                    with Budget(path, self.source, self.target):
                        pass
                self.assertEqual(budget.reserve(), 1)
            with Budget(path, self.source, self.target) as budget:
                self.assertEqual(budget.read()["used"], 1)
                self.assertEqual(budget.read()["status"], "in_progress")
                with self.assertRaisesRegex(ValueError, "unfinished"):
                    budget.reserve()
                budget.finish(retryable=True)
                self.assertEqual(budget.reserve(), 2)
                budget.finish(retryable=True)
                with self.assertRaisesRegex(ValueError, "exhausted"):
                    budget.reserve()
            with Budget(path, self.source, "192.0.2.21") as budget:
                with self.assertRaisesRegex(ValueError, "endpoint"):
                    budget.read()
            (path / "budget.json").write_text("{", encoding="utf-8")
            with Budget(path, self.source, self.target) as budget:
                with self.assertRaisesRegex(ValueError, "invalid"):
                    budget.read()

    def test_retry_budget_and_backoff_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            with Budget(Path(directory), self.source, self.target) as budget:
                budget.initialize(3)

            def interrupted(*args, **kwargs):
                kwargs["on_retryable"]("live")
                return 1

            with Budget(Path(directory), self.source, self.target) as budget, \
                 patch("netvue_bridge.supervisor.wait_for_rtsp", return_value=True), \
                 patch("netvue_bridge.supervisor.run_sessions", side_effect=interrupted) as run, \
                 patch("netvue_bridge.supervisor.time.sleep") as sleep, patch("builtins.print"):
                self.assertEqual(supervise(budget, self.rtsp), 1)
                self.assertEqual(run.call_count, 3)
                self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10])
                self.assertEqual(budget.read()["used"], 3)
            with Budget(Path(directory), self.source, self.target) as budget, \
                 patch("netvue_bridge.supervisor.run_sessions") as run, patch("builtins.print"):
                self.assertEqual(supervise(budget, self.rtsp), 1)
                run.assert_not_called()
        self.assertEqual(retry_delay(20), 60)

    def test_non_retryable_and_unavailable_rtsp_do_not_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            with Budget(Path(directory), self.source, self.target) as budget:
                budget.initialize(2)
                with patch("netvue_bridge.supervisor.wait_for_rtsp", return_value=False), \
                     patch("netvue_bridge.supervisor.run_sessions") as run:
                    self.assertEqual(supervise(budget, self.rtsp), 1)
                    run.assert_not_called()
                    self.assertEqual(budget.read()["used"], 0)
                with patch("netvue_bridge.supervisor.wait_for_rtsp", return_value=True), \
                     patch("netvue_bridge.supervisor.run_sessions", return_value=1) as run, \
                     patch("builtins.print"):
                    self.assertEqual(supervise(budget, self.rtsp), 1)
                    self.assertEqual(run.call_count, 1)
                    self.assertEqual(budget.read()["used"], 1)
                    self.assertEqual(budget.read()["status"], "blocked")
            with Budget(Path(directory), self.source, self.target) as budget, \
                 patch("netvue_bridge.supervisor.wait_for_rtsp", return_value=True), \
                 patch("netvue_bridge.supervisor.run_sessions", return_value=1) as run, \
                 patch("netvue_bridge.supervisor.time.sleep") as sleep, patch("builtins.print"):
                self.assertEqual(supervise(budget, self.rtsp), 1)
                sleep.assert_not_called()
                run.assert_not_called()
                self.assertEqual(budget.read()["used"], 1)

    def test_crash_leaves_attempt_blocked_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with Budget(path, self.source, self.target) as budget:
                budget.initialize(2)
                with patch("netvue_bridge.supervisor.wait_for_rtsp", return_value=True), \
                     patch("netvue_bridge.supervisor.run_sessions",
                           side_effect=RuntimeError("simulated crash")):
                    with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                        supervise(budget, self.rtsp)
            with Budget(path, self.source, self.target) as budget, \
                 patch("netvue_bridge.supervisor.run_sessions") as run:
                self.assertEqual(budget.read()["status"], "in_progress")
                self.assertEqual(supervise(budget, self.rtsp), 1)
                run.assert_not_called()

    def test_sigterm_unwinds_running_session_and_keeps_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            with Budget(path, self.source, self.target) as budget:
                budget.initialize(1)
            marker = path / "ready"
            script = (
                "import sys,time\n"
                "from pathlib import Path\n"
                "from netvue_bridge import supervisor\n"
                "marker = sys.argv[1]\n"
                "def fake(*args, **kwargs):\n"
                "    Path(marker).touch()\n"
                "    try: time.sleep(30)\n"
                "    finally: print('session-closed', flush=True)\n"
                "supervisor.run_sessions = fake\n"
                "supervisor.wait_for_rtsp = lambda port: True\n"
                "sys.argv = ['supervisor', '_worker', '--source', sys.argv[2], "
                "'--target', sys.argv[3], '--state-dir', sys.argv[4]]\n"
                "raise SystemExit(supervisor.main())\n"
            )
            process = subprocess.Popen(
                [sys.executable, "-B", "-u", "-c", script, str(marker), self.source,
                 self.target, str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists(), "Synthetic session did not start")
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130, stderr)
                self.assertIn("session-closed", stdout)
                with Budget(path, self.source, self.target) as budget:
                    self.assertEqual(budget.read()["used"], 1)
                    self.assertEqual(budget.read()["status"], "in_progress")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

    def test_watchdog_gracefully_stops_worker_at_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            script = (
                "import signal,socket,sys,time\n"
                "from pathlib import Path\n"
                "def stop(signum, frame): raise KeyboardInterrupt\n"
                "signal.signal(signal.SIGTERM, stop)\n"
                "left,right=socket.socketpair()\n"
                "try:\n"
                "    Path(sys.argv[1]).touch()\n"
                "    time.sleep(20)\n"
                "finally:\n"
                "    left.close(); right.close()\n"
                "    Path(sys.argv[2]).touch()\n"
            )
            start = time.monotonic()
            with patch("builtins.print"):
                result = run_bounded([sys.executable, "-c", script,
                                      str(path / "ready"), str(path / "closed")], 2)
            self.assertEqual(result, 124)
            self.assertTrue((path / "ready").exists())
            self.assertTrue((path / "closed").exists())
            self.assertLess(time.monotonic() - start, 2.5)

    def test_watchdog_forces_only_unresponsive_child(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "pid"
            script = (
                "import os,signal,sys,time\n"
                "from pathlib import Path\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
                "time.sleep(20)\n"
            )
            start = time.monotonic()
            with patch("builtins.print") as log:
                result = run_bounded([sys.executable, "-c", script, str(marker)], 1.2)
            self.assertEqual(result, 124)
            self.assertTrue(marker.exists())
            self.assertTrue(any("forcing project worker" in str(call) for call in log.call_args_list))
            self.assertLess(time.monotonic() - start, 1.7)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)

    def test_watchdog_validates_duration_and_preserves_child_exit(self):
        with patch("netvue_bridge.supervisor.subprocess.Popen") as launch:
            for duration in (0, -1, float("nan"), float("inf"), 604801):
                with self.assertRaises(ValueError):
                    run_bounded(["unused"], duration)
            launch.assert_not_called()
        self.assertEqual(run_bounded([sys.executable, "-c", "raise SystemExit(7)"], 2), 7)

    def test_bounded_cli_stops_preflight_without_using_budget(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as port:
            port.bind(("127.0.0.1", 0))
            path = Path(directory)
            with Budget(path, self.source, self.target) as budget:
                budget.initialize(1)
            result = subprocess.run(
                [sys.executable, "-B", "-m", "netvue_bridge.supervisor", "run",
                 "--source", self.source, "--target", self.target, "--state-dir", str(path),
                 "--rtsp", f"rtsp://127.0.0.1:{port.getsockname()[1]}/netvue2",
                 "--max-duration", "1"],
                capture_output=True, text=True, timeout=4,
            )
            self.assertEqual(result.returncode, 124, result.stderr)
            with Budget(path, self.source, self.target) as budget:
                self.assertEqual(budget.read()["used"], 0)
                self.assertEqual(budget.read()["status"], "ready")


if __name__ == "__main__":
    unittest.main()
