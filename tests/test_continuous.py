"""Tests for the unified state, retry supervisor and bounded test mode."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if os.name == "posix":
    from netvue_bridge.budget import Budget, retry_delay
    from netvue_bridge.continuous import (
        active_session_pids, main, resource_snapshot, run_bounded, run_worker, supervise,
    )


@unittest.skipUnless(os.name == "posix", "supervisor requires Linux file locks")
class SupervisorTests(unittest.TestCase):
    source = "192.168.10.1"
    target = "192.168.10.2"
    rtsp = "rtsp://127.0.0.1:8554/netvue2"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.wall, self.boot, self.boot_id = 1700000000.0, 1000000.0, "test-boot"
        clock = patch("netvue_bridge.budget.clock_sample", side_effect=self.sample)
        clock.start()
        self.addCleanup(clock.stop)

    def sample(self):
        return self.wall, self.boot, self.boot_id

    def advance(self, seconds):
        self.wall += seconds
        self.boot += seconds

    def test_single_budget_is_exclusive_durable_and_fails_closed(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            with self.assertRaises(FileExistsError):
                budget.initialize()
            with self.assertRaises(BlockingIOError):
                with Budget(self.path, self.source, self.target):
                    pass
            self.assertEqual(budget.reserve(), 1)
            budget.stage("STREAMING")
        with Budget(self.path, self.source, self.target) as budget:
            self.assertEqual(budget.read()["status"], "in_progress")
            with self.assertRaisesRegex(ValueError, "BUDGET_IN_PROGRESS"):
                budget.reserve()
        with Budget(self.path, self.source, self.target) as budget:
            state = budget.read()
            state["status"] = "blocked"
            state["stage"] = "BLOCKED"
            budget._write(state)
            with self.assertRaisesRegex(ValueError, "BUDGET_BLOCKED"):
                budget.reserve()

    def test_budget_file_rejects_duplicate_keys_and_unbounded_numbers(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            raw = budget.path.read_text()
            budget.path.write_text(raw.replace('"version": 4', '"version": 4, "version": 4'))
            with self.assertRaisesRegex(ValueError, "Budget file is invalid"):
                budget.read()
            budget.path.write_text(raw)
            state = json.loads(raw)
            state["wait_until"] = 10 ** 400
            budget.path.write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError, "Budget state or endpoint is invalid"):
                budget.read()

    def test_file_lock_excludes_a_separate_process(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            script = (
                "from pathlib import Path; import sys; "
                "from netvue_bridge.budget import Budget; "
                "b=Budget(Path(sys.argv[1]), sys.argv[2], sys.argv[3]); "
                "b.__enter__()"
            )
            result = subprocess.run(
                [sys.executable, "-c", script, str(self.path), self.source, self.target],
                capture_output=True, text=True, timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("BlockingIOError", result.stderr)

    def test_transient_errors_use_persisted_exponential_backoff_without_quotas(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            for attempt in range(1, 13):
                self.assertEqual(budget.reserve(), attempt)
                budget.finish_retryable("video" if attempt % 2 else "live")
                if attempt < 12:
                    self.advance(retry_delay(attempt))
            state = budget.read()
            self.assertEqual((state["total"], state["status"], state["streak"]), (12, "ready", 12))
        self.assertEqual([retry_delay(n) for n in range(1, 7)], [5, 15, 60, 300, 900, 900])

    def test_backoff_wait_and_stable_stream_reset_only_retry_streak(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
            budget.finish_retryable("connect")
            with self.assertRaisesRegex(ValueError, "backoff"):
                budget.reserve()
            self.advance(5)
            budget.reserve()
            budget.stage("STREAMING")
            budget.mark_stable()
            self.assertEqual((budget.read()["total"], budget.read()["streak"]), (2, 0))

    def test_clock_ambiguity_on_same_boot_fails_before_network(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
        self.wall += 100
        with Budget(self.path, self.source, self.target) as budget, \
             patch("netvue_bridge.continuous.wait_for_rtsp") as network:
            with self.assertRaisesRegex(ValueError, "CLOCK_AMBIGUOUS"):
                supervise(budget, self.rtsp)
            network.assert_not_called()

    def test_rtsp_unavailable_uses_backoff_and_retries_without_name_error(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            with patch("netvue_bridge.continuous.wait_for_rtsp",
                       side_effect=[False, True]) as wait, \
                    patch("netvue_bridge.continuous.run_worker",
                          return_value="discovery") as worker, \
                    patch("netvue_bridge.continuous.time.sleep") as sleep:
                self.assertEqual(supervise(budget, self.rtsp, max_attempts=1), 0)
            self.assertEqual(wait.call_count, 2)
            worker.assert_called_once()
            sleep.assert_called_once_with(5)
            state = budget.read()
            self.assertEqual((state["total"], state["last_error"]), (1, "discovery"))

    def test_reboot_recovers_in_progress_with_backoff(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
            budget.stage("STREAMING")
        previous_wait = self.boot
        self.wall += 60
        self.boot = 5
        self.boot_id = "other-boot"
        with Budget(self.path, self.source, self.target) as budget:
            self.assertTrue(budget.recover_after_restart())
            state = budget.read()
        self.assertEqual((state["status"], state["stage"], state["streak"], state["total"]),
                         ("ready", "RECONNECT_WAIT", 1, 1))
        self.assertEqual(state["wait_until"], self.boot + retry_delay(1))
        self.assertEqual(state["boot"], self.boot)
        self.assertNotEqual(state["boot"], previous_wait)

    def test_supervisor_resumes_after_reboot_and_observes_backoff(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
        self.wall += 60
        self.boot = 5
        self.boot_id = "other-boot"

        def sleep(seconds):
            self.advance(seconds)

        with Budget(self.path, self.source, self.target) as budget, \
             patch("netvue_bridge.continuous.wait_for_rtsp", return_value=True) as network, \
             patch("netvue_bridge.continuous.run_worker", return_value="connect") as worker, \
             patch("netvue_bridge.continuous.time.sleep", side_effect=sleep), \
             patch("builtins.print"):
            self.assertEqual(supervise(budget, self.rtsp, max_attempts=1), 0)
        network.assert_called_once_with(8554)
        worker.assert_called_once()
        with Budget(self.path, self.source, self.target) as budget:
            state = budget.read()
        self.assertEqual((state["total"], state["status"], state["streak"]),
                         (2, "ready", 2))

    def test_reboot_rebases_existing_retry_backoff(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
            budget.finish_retryable("video")
            old_remaining = budget.read()["wait_until"] - self.boot
        self.wall += 60
        self.boot = 5
        self.boot_id = "other-boot"
        with Budget(self.path, self.source, self.target) as budget:
            self.assertTrue(budget.recover_after_restart())
            state = budget.read()
        self.assertEqual(state["wait_until"], self.boot + old_remaining)
        self.assertEqual((state["status"], state["last_error"]), ("ready", "video"))

    def test_reboot_with_backwards_wall_clock_remains_unmodified(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            original = budget.read()
        self.boot_id = "other-boot"
        self.wall -= 10
        with Budget(self.path, self.source, self.target) as budget:
            with self.assertRaisesRegex(ValueError, "CLOCK_AMBIGUOUS"):
                budget.recover_after_restart()
            self.assertEqual(budget.read(), original)

    def test_container_restart_recovers_in_progress_on_same_boot(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
        self.advance(2)
        with Budget(self.path, self.source, self.target) as budget:
            self.assertTrue(budget.recover_after_restart())
            state = budget.read()
        self.assertEqual((state["status"], state["streak"], state["total"]),
                         ("ready", 1, 1))
        self.assertEqual(state["last_error"], "connect")

    def test_active_old_session_prevents_restart_recovery(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            budget.reserve()
        self.boot_id = "other-boot"
        with Budget(self.path, self.source, self.target) as budget, \
             patch("netvue_bridge.continuous.active_session_pids", return_value=[123]):
            with self.assertRaisesRegex(RuntimeError, "ACTIVE_SESSION"):
                supervise(budget, self.rtsp)
            self.assertEqual(budget.read()["status"], "in_progress")

    def test_blocked_state_is_not_recovered_after_reboot(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            state = budget.read()
            state.update(status="blocked", stage="BLOCKED")
            budget._write(state)
        self.boot_id = "other-boot"
        with Budget(self.path, self.source, self.target) as budget, \
             patch("netvue_bridge.continuous.wait_for_rtsp") as network:
            self.assertEqual(supervise(budget, self.rtsp), 1)
            self.assertEqual(budget.read()["status"], "blocked")
            network.assert_not_called()

    def test_corrupt_state_is_preserved_and_stops_before_network(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
        corrupt = b'{"version":4'
        (self.path / "budget.json").write_bytes(corrupt)
        with Budget(self.path, self.source, self.target) as budget, \
             patch("netvue_bridge.continuous.wait_for_rtsp") as network:
            with self.assertRaisesRegex(ValueError, "invalid"):
                supervise(budget, self.rtsp)
            network.assert_not_called()
        self.assertEqual((self.path / "budget.json").read_bytes(), corrupt)

    def test_active_session_scan_matches_camera_and_known_workers(self):
        proc = self.path / "proc"
        for pid, command in (
                (101, [b"python", b"-m", b"netvue_bridge.continuous", b"_session",
                       b"--target", self.target.encode()]),
                (102, [b"python", b"-m", b"netvue_bridge.supervisor", b"_worker",
                       b"--target", self.target.encode()]),
                (103, [b"python", b"-m", b"netvue_bridge.continuous", b"run",
                       b"--target", b"192.0.2.99"])):
            directory = proc / str(pid)
            directory.mkdir(parents=True)
            (directory / "cmdline").write_bytes(b"\0".join(command) + b"\0")
        self.assertEqual(active_session_pids(self.target, proc), [101, 102])

    def test_v2_migration_audits_and_preserves_ambiguous_state_as_blocked(self):
        legacy = {"version": 2, "source": self.source, "target": self.target,
                  "limit": 4, "used": 2, "status": "ready"}
        (self.path / "budget.json").write_text(json.dumps(legacy), encoding="utf-8")
        with Budget(self.path, self.source, self.target) as budget:
            with self.assertRaisesRegex(ValueError, "version"):
                budget.read()
            audit = budget.migrate_legacy()
            state = budget.read()
            self.assertEqual((state["version"], state["status"], state["total"]), (4, "blocked", 2))
        record = json.loads(audit.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(record["original_json"]), legacy)
        self.assertEqual(record["after"]["status"], "blocked")

    def test_v3_migration_is_explicit_and_drops_quotas_but_keeps_backoff(self):
        legacy = {"version": 3, "source": self.source, "target": self.target,
                  "status": "ready", "stage": "RECONNECT_WAIT",
                  "attempts": [self.boot - 60] * 8, "streak": 2, "total": 8,
                  "last_reconnect": self.wall - 60, "last_error": "live",
                  "wait_until": self.boot + 5, "wall": self.wall - 60,
                  "boot": self.boot - 60, "boot_id": self.boot_id}
        (self.path / "budget.json").write_text(json.dumps(legacy), encoding="utf-8")
        with Budget(self.path, self.source, self.target) as budget:
            with self.assertRaisesRegex(ValueError, "version"):
                budget.read()
            budget.migrate_legacy()
            self.assertEqual(budget.read()["status"], "ready")
            self.advance(5)
            self.assertEqual(budget.reserve(), 9)

    def test_migration_write_failure_keeps_original_state(self):
        legacy = {"version": 2, "source": self.source, "target": self.target,
                  "limit": 4, "used": 1, "status": "ready"}
        original = json.dumps(legacy).encode()
        (self.path / "budget.json").write_bytes(original)
        with Budget(self.path, self.source, self.target) as budget, \
             patch.object(budget, "_write", side_effect=OSError("interrupted")):
            with self.assertRaisesRegex(OSError, "interrupted"):
                budget.migrate_legacy()
            self.assertEqual(budget.path.read_bytes(), original)
            self.assertEqual(len(list(self.path.glob("migration-*.json"))), 1)

    def test_v3_in_progress_migration_remains_blocked(self):
        legacy = {"version": 3, "source": self.source, "target": self.target,
                  "status": "in_progress", "stage": "STREAMING",
                  "attempts": [self.boot], "streak": 0, "total": 1,
                  "last_reconnect": None, "last_error": None,
                  "wait_until": self.boot, "wall": self.wall,
                  "boot": self.boot, "boot_id": self.boot_id}
        (self.path / "budget.json").write_text(json.dumps(legacy), encoding="utf-8")
        with Budget(self.path, self.source, self.target) as budget:
            budget.migrate_legacy()
            self.assertEqual((budget.read()["status"], budget.read()["stage"]),
                             ("blocked", "BLOCKED"))

    def test_migration_rejects_malformed_v3_without_touching_source(self):
        legacy = {"version": 3, "source": self.source, "target": self.target,
                  "status": "ready", "stage": [], "attempts": [], "streak": 0,
                  "total": 0, "last_reconnect": None, "last_error": None,
                  "wait_until": self.boot, "wall": self.wall, "boot": self.boot,
                  "boot_id": self.boot_id}
        original = json.dumps(legacy).encode()
        (self.path / "budget.json").write_bytes(original)
        with Budget(self.path, self.source, self.target) as budget:
            with self.assertRaisesRegex(ValueError, "invalid"):
                budget.migrate_legacy()
            self.assertEqual(budget.path.read_bytes(), original)
            self.assertEqual(list(self.path.glob("migration-*.json")), [])

    def test_supervisor_retries_video_failures_and_bounded_attempts_stop(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()

            def wait(seconds):
                self.advance(seconds)

            with patch("netvue_bridge.continuous.wait_for_rtsp", return_value=True), \
                 patch("netvue_bridge.continuous.run_worker",
                       side_effect=["video", "connect", "live"]) as worker, \
                 patch("netvue_bridge.continuous.time.sleep", side_effect=wait), \
                 patch("builtins.print"):
                self.assertEqual(supervise(budget, self.rtsp, max_attempts=3), 0)
                self.assertEqual(worker.call_count, 3)
            self.assertEqual(budget.read()["total"], 3)
            self.assertEqual(budget.read()["status"], "ready")

    def test_security_failure_blocks_and_does_not_retry(self):
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            with patch("netvue_bridge.continuous.wait_for_rtsp", return_value=True), \
                 patch("netvue_bridge.continuous.run_worker", return_value="blocked") as worker, \
                 patch("builtins.print"):
                self.assertEqual(supervise(budget, self.rtsp), 1)
                worker.assert_called_once()
            self.assertEqual(budget.read()["status"], "blocked")

    def test_test_cli_requires_both_bounds_and_uses_watchdog(self):
        args = ["continuous", "test", "--source", self.source, "--target", self.target,
                "--state-dir", str(self.path)]
        with patch("sys.argv", args), patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                main()
        with patch("sys.argv", args + ["--max-duration", "2", "--max-attempts", "1"]), \
             patch("netvue_bridge.continuous.run_bounded", return_value=124) as watchdog:
            self.assertEqual(main(), 124)
            watchdog.assert_called_once()

    def test_bounded_cli_stops_preflight_without_reserving_an_attempt(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            rtsp = f"rtsp://127.0.0.1:{listener.getsockname()[1]}/netvue2"
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
        result = subprocess.run(
            [sys.executable, "-B", "-m", "netvue_bridge.continuous", "test",
             "--source", self.source, "--target", self.target,
             "--state-dir", str(self.path), "--rtsp", rtsp,
             "--max-duration", "1", "--max-attempts", "1"],
            capture_output=True, text=True, timeout=4,
        )
        self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
        with Budget(self.path, self.source, self.target) as budget:
            self.assertEqual((budget.read()["total"], budget.read()["status"]), (0, "ready"))

    def test_watchdog_stops_child_at_deadline_and_preserves_exit_code(self):
        with patch("builtins.print"):
            result = run_bounded([sys.executable, "-c", "import time; time.sleep(20)"], 1.2)
        self.assertEqual(result, 124)
        self.assertEqual(run_bounded([sys.executable, "-c", "raise SystemExit(7)"], 2), 7)

    def test_resource_snapshot_requires_finite_cgroup_limit(self):
        (self.path / "memory.current").write_text("123\n", encoding="ascii")
        (self.path / "memory.max").write_text("1000\n", encoding="ascii")
        (self.path / "cpu.stat").write_text("usage_usec 456\n", encoding="ascii")
        with patch("netvue_bridge.continuous.Path", return_value=self.path):
            self.assertEqual(resource_snapshot(), {
                "memory_bytes": 123, "memory_limit_bytes": 1000, "cpu_usec": 456,
            })
            (self.path / "memory.max").write_text("max\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "finite cgroup memory limit"):
                resource_snapshot()

    def test_worker_stops_at_memory_threshold_and_on_video_stall(self):
        original_popen = subprocess.Popen
        children = []
        with Budget(self.path, self.source, self.target) as budget:
            budget.initialize()
            for metrics, result in (
                ({"memory_bytes": 95, "memory_limit_bytes": 100, "cpu_usec": 1}, "ambiguous"),
                ({"memory_bytes": 1, "memory_limit_bytes": 100, "cpu_usec": 1}, "live"),
            ):
                with tempfile.TemporaryDirectory() as state_dir, \
                     Budget(Path(state_dir), self.source, self.target) as attempt_budget:
                    attempt_budget.initialize()
                    attempt_budget.reserve()

                    def launch(command, **kwargs):
                        fd = command[command.index("--event-fd") + 1]
                        if result == "live":
                            script = ("import os,sys,time; "
                                      "os.write(int(sys.argv[1]), "
                                      "b'{\"event\":\"stage\",\"stage\":\"live\"}\\n'); "
                                      "time.sleep(20)")
                        else:
                            script = "import time; time.sleep(20)"
                        child = original_popen([sys.executable, "-c", script, fd], **kwargs)
                        children.append(child)
                        return child

                    with patch("netvue_bridge.continuous.subprocess.Popen", side_effect=launch), \
                         patch("netvue_bridge.continuous.HEARTBEAT_TIMEOUT", 0.1), \
                         patch("netvue_bridge.continuous.resource_snapshot", return_value=metrics):
                        self.assertEqual(run_worker(attempt_budget, self.rtsp, 0.1), result)
                    self.assertIsNotNone(children[-1].poll())
                    self.assertEqual(attempt_budget.read()["status"], "in_progress")


if __name__ == "__main__":
    unittest.main()
