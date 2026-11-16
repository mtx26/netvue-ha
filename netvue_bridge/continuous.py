"""Fail-closed continuous supervisor with a persistent rolling connection budget."""

import argparse
import ctypes
import json
import math
import os
import random
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from .rtsp import validate_local_rtsp_url
from .stream import run_sessions
from .supervisor import Budget, wait_for_rtsp

DAY = 86400
WEEK = 7 * DAY
HEARTBEAT_TIMEOUT = 30


def clock_sample() -> tuple[float, float, str]:
    return (time.time(), time.clock_gettime(time.CLOCK_BOOTTIME),
            Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip())


def reconnect_delay(streak: int) -> float:
    if streak < 1:
        raise ValueError("Invalid reconnect streak")
    base = (30, 120, 300)[min(streak, 3) - 1]
    if streak > 3:
        base = min(1800, 300 * 2 ** min(streak - 3, 3))
    return min(1800, base * random.SystemRandom().uniform(1, 1.2))


def resource_snapshot() -> dict[str, int]:
    root = Path("/sys/fs/cgroup")
    current = int((root / "memory.current").read_text(encoding="ascii").strip())
    maximum_text = (root / "memory.max").read_text(encoding="ascii").strip()
    if maximum_text == "max":
        raise ValueError("A finite cgroup memory limit is required")
    maximum = int(maximum_text)
    stats = dict(line.split() for line in (root / "cpu.stat").read_text(encoding="ascii").splitlines())
    cpu_usec = int(stats["usage_usec"])
    if current < 0 or maximum <= 0 or cpu_usec < 0:
        raise ValueError("Invalid cgroup resource statistics")
    return {"memory_bytes": current, "memory_limit_bytes": maximum, "cpu_usec": cpu_usec}


class RollingBudget(Budget):
    def initialize(self) -> None:
        if self.lock_file is None:
            raise RuntimeError("Budget lock is required")
        wall, boot, boot_id = clock_sample()
        state = {"version": 3, "source": self.source, "target": self.target,
                 "status": "ready", "stage": "READY", "attempts": [],
                 "streak": 0, "total": 0, "last_reconnect": None, "last_error": None,
                 "wait_until": 0.0, "wall": wall, "boot": boot, "boot_id": boot_id}
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(state, file)
            file.flush()
            os.fsync(file.fileno())
        self._sync_directory()

    def read(self) -> dict:
        if self.lock_file is None:
            raise RuntimeError("Budget lock is required")
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as file:
            raw = file.read(4097)
        if len(raw) > 4096:
            raise ValueError("Rolling budget file is too large")
        try:
            state = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Rolling budget file is invalid") from exc
        keys = {"version", "source", "target", "status", "stage", "attempts",
                "streak", "total", "last_reconnect", "last_error",
                "wait_until", "wall", "boot", "boot_id"}
        number = lambda value: type(value) in (int, float) and math.isfinite(value) and value >= 0
        if (not isinstance(state, dict) or set(state) != keys
                or type(state["version"]) is not int or state["version"] != 3
                or state["source"] != self.source or state["target"] != self.target
                or state["status"] not in ("ready", "in_progress", "blocked")
                or state["stage"] not in ("READY", "CONNECTING", "AUTHENTICATING",
                                          "WAITING_VIDEO", "STREAMING", "RECONNECT_WAIT", "BLOCKED")
                or not isinstance(state["attempts"], list) or len(state["attempts"]) > 8
                or not all(number(value) for value in state["attempts"])
                or state["attempts"] != sorted(state["attempts"])
                or type(state["streak"]) is not int or not 0 <= state["streak"] <= 1000000
                or type(state["total"]) is not int or state["total"] < len(state["attempts"])
                or (state["last_reconnect"] is not None and not number(state["last_reconnect"]))
                or state["last_error"] not in (None, "discovery", "connect", "live")
                or not all(number(state[key]) for key in ("wait_until", "wall", "boot"))
                or state["wait_until"] > state["boot"] + 1800
                or not isinstance(state["boot_id"], str) or not state["boot_id"]
                or (state["status"] == "in_progress" and not state["attempts"])
                or (state["status"] == "in_progress" and state["stage"] not in
                    ("CONNECTING", "AUTHENTICATING", "WAITING_VIDEO", "STREAMING"))
                or (state["status"] == "blocked" and state["stage"] != "BLOCKED")
                or (state["status"] == "ready" and state["stage"] not in
                    ("READY", "RECONNECT_WAIT"))
                or (state["stage"] == "READY" and
                    (state["total"] != 0 or state["attempts"] or state["streak"] != 0))
                or (state["stage"] == "RECONNECT_WAIT" and
                    (state["total"] == 0 or state["streak"] == 0
                     or state["last_error"] is None))
                or any(value > state["boot"] for value in state["attempts"])):
            raise ValueError("Rolling budget state or endpoint is invalid")
        return state

    def checked(self) -> tuple[dict, float, float]:
        state = self.read()
        wall, boot, boot_id = clock_sample()
        if (boot_id != state["boot_id"] or boot < state["boot"]
                or abs((wall - state["wall"]) - (boot - state["boot"])) > 5):
            raise ValueError("CLOCK_AMBIGUOUS; manual review required")
        return state, wall, boot

    def reserve(self) -> int:
        state, wall, boot = self.checked()
        if state["status"] != "ready":
            raise ValueError("BUDGET_" + state["status"].upper())
        if boot < state["wait_until"]:
            raise ValueError("Reconnect backoff has not elapsed")
        recent = [item for item in state["attempts"] if boot - item < WEEK]
        if sum(boot - item < DAY for item in recent) >= 3 or len(recent) >= 8:
            state.update(status="blocked", stage="BLOCKED", wall=wall, boot=boot)
            self._write(state)
            raise ValueError("BUDGET_EXHAUSTED; manual review required")
        recent.append(boot)
        total = state["total"] + 1
        state.update(attempts=recent, status="in_progress", stage="CONNECTING", total=total,
                     last_reconnect=wall if total > 1 else None,
                     wall=wall, boot=boot)
        self._write(state)
        return total

    def stage(self, stage: str) -> None:
        state, wall, boot = self.checked()
        if state["status"] != "in_progress":
            raise ValueError("No attempt in progress")
        if stage not in ("CONNECTING", "AUTHENTICATING", "WAITING_VIDEO", "STREAMING"):
            raise ValueError("Invalid session stage")
        state.update(stage=stage, wall=wall, boot=boot)
        self._write(state)

    def finish_retryable(self, stage: str) -> None:
        state, wall, boot = self.checked()
        if state["status"] != "in_progress":
            raise ValueError("No attempt in progress")
        if stage not in ("discovery", "connect", "live"):
            raise ValueError("Invalid retryable stage")
        streak = state["streak"] + 1
        state.update(status="ready", stage="RECONNECT_WAIT", streak=streak,
                     last_error=stage, wait_until=boot + reconnect_delay(streak),
                     wall=wall, boot=boot)
        self._write(state)

    def block(self) -> None:
        state, wall, boot = self.checked()
        state.update(status="blocked", stage="BLOCKED", wall=wall, boot=boot)
        self._write(state)


def worker(source: str, target: str, rtsp: str, fd: int, parent: int) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot configure parent death signal")
    if os.getppid() != parent:
        raise RuntimeError("Supervisor disappeared before worker startup")
    signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    last_sent = 0.0
    started = time.monotonic()

    def send(event: dict) -> None:
        os.write(fd, (json.dumps(event, separators=(",", ":")) + "\n").encode())

    def stage(value: str) -> None:
        send({"event": "stage", "stage": value})

    def video(publisher) -> None:
        nonlocal last_sent
        now = time.monotonic()
        if last_sent and now - last_sent < 5:
            return
        last_sent = now
        metrics = publisher.safe_metrics()
        metrics.update(last_video_unix=time.time(), session_seconds=round(now - started, 2),
                       no_video_seconds=0,
                       fps=round(metrics["video_packets"] / max(now - started, 0.001), 2))
        send({"event": "video", "metrics": metrics})

    try:
        retryable = []
        result = run_sessions(source, target, rtsp, float("inf"), max_attempts=1,
                              on_stage=stage, on_video=video, on_retryable=retryable.append)
        if result == 1 and retryable:
            send({"event": "retryable", "stage": retryable[-1]})
        elif result == 1:
            send({"event": "blocked"})
        return result
    finally:
        os.close(fd)


def run_worker(budget: RollingBudget, rtsp: str) -> str:
    read_fd, write_fd = os.pipe()
    command = [sys.executable, "-B", "-m", "netvue_bridge.continuous", "_session",
               "--source", budget.source, "--target", budget.target, "--rtsp", rtsp,
               "--event-fd", str(write_fd), "--parent-pid", str(os.getpid())]
    process = None
    try:
        process = subprocess.Popen(command, pass_fds=(write_fd,))
        os.close(write_fd)
        write_fd = -1
        buffer = b""
        last_progress = time.monotonic()
        last_report = last_progress
        last_resource_check = 0.0
        resources = None
        outcome = None
        metrics = None
        last_published = 0
        last_publication = last_progress
        streaming = False
        while process.poll() is None:
            ready, _, _ = select.select([read_fd], [], [], 1)
            if ready:
                data = os.read(read_fd, 8192)
                if data:
                    buffer += data
                    if len(buffer) > 16384:
                        raise ValueError("Worker event buffer exceeded")
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        event = json.loads(line)
                        if event["event"] == "stage":
                            stages = {"discovery": "CONNECTING", "connect": "CONNECTING",
                                      "authentication": "AUTHENTICATING", "live": "WAITING_VIDEO"}
                            budget.stage(stages[event["stage"]])
                            print("continuous: state=" + stages[event["stage"]], flush=True)
                        elif event["event"] == "video":
                            metrics = event["metrics"]
                            published = metrics["published_frames"]
                            if published > last_published:
                                last_published = published
                                last_publication = time.monotonic()
                                if not streaming:
                                    budget.stage("STREAMING")
                                    streaming = True
                                    print("continuous: state=STREAMING", flush=True)
                        elif event["event"] == "retryable":
                            outcome = event["stage"]
                        elif event["event"] == "blocked":
                            outcome = "blocked"
                        else:
                            raise ValueError("Invalid worker event")
                        last_progress = time.monotonic()
            now = time.monotonic()
            if now - last_resource_check >= 5:
                resources = resource_snapshot()
                last_resource_check = now
                if resources["memory_bytes"] * 100 >= resources["memory_limit_bytes"] * 95:
                    print("continuous: MEMORY_LIMIT_NEAR; manual review required", flush=True)
                    return "ambiguous"
            if now - last_progress > HEARTBEAT_TIMEOUT:
                print("continuous: WATCHDOG_TIMEOUT; worker stalled, manual review required",
                      flush=True)
                return "ambiguous"
            if metrics and now - last_publication > HEARTBEAT_TIMEOUT:
                print("continuous: PUBLICATION_STALLED; manual review required", flush=True)
                return "ambiguous"
            if now - last_report >= 30:
                state = budget.read()
                print("continuous_health: " + json.dumps({
                    "state": "STREAMING" if streaming else "WAITING_VIDEO",
                    "seconds_since_progress": round(now - last_progress, 1),
                    "reconnections": max(0, state["total"] - 1),
                    "last_reconnect_unix": state["last_reconnect"],
                    "last_error_stage": state["last_error"],
                    "resources": resources,
                    "metrics": metrics,
                }), flush=True)
                last_report = now
        # Drain a final event sent immediately before exit.
        while select.select([read_fd], [], [], 0)[0]:
            data = os.read(read_fd, 8192)
            if not data:
                break
            buffer += data
        for line in buffer.splitlines():
            event = json.loads(line)
            if event["event"] == "retryable":
                outcome = event["stage"]
            elif event["event"] == "blocked":
                outcome = "blocked"
        if process.returncode == 1 and outcome in ("discovery", "connect", "live"):
            print("continuous: retryable interruption at " + outcome, flush=True)
            return outcome
        if process.returncode == 1 and outcome == "blocked":
            print("continuous: non-retryable session failure", flush=True)
            return "blocked"
        print(f"continuous: unexpected worker exit={process.returncode}", flush=True)
        return "ambiguous"
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("continuous: forcing project worker to stop", flush=True)
                process.kill()
                process.wait()
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def supervise_continuous(budget: RollingBudget, rtsp: str) -> int:
    validate_local_rtsp_url(rtsp)
    port = urlsplit(rtsp).port
    if port is None:
        raise ValueError("RTSP port required")
    while True:
        state, wall, boot = budget.checked()
        if state["status"] != "ready":
            print("continuous: BUDGET_" + state["status"].upper(), flush=True)
            return 1
        recent = [item for item in state["attempts"] if boot - item < WEEK]
        if len(recent) >= 8 or sum(boot - item < DAY for item in recent) >= 3:
            budget.block()
            print("continuous: BUDGET_EXHAUSTED; manual review required", flush=True)
            return 1
        remaining = state["wait_until"] - boot
        if remaining > 0:
            print(f"continuous: state=RECONNECT_WAIT remaining={remaining:.1f}s", flush=True)
            time.sleep(remaining)
            continue
        if not wait_for_rtsp(port):
            print("continuous: RTSP_UNAVAILABLE; stopped", flush=True)
            return 1
        count = budget.reserve()
        print(f"continuous: reserved attempt {count}; "
              f"reconnections={max(0, count - 1)}", flush=True)
        result = run_worker(budget, rtsp)
        if result in ("discovery", "connect", "live"):
            budget.finish_retryable(result)
        elif result == "blocked":
            budget.block()
            return 1
        else:
            return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent continuous Netvue supervisor")
    parser.add_argument("action", choices=("init", "run", "_session"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--state-dir", type=Path, default=Path("/state"))
    parser.add_argument("--rtsp", default="rtsp://127.0.0.1:8554/netvue2")
    parser.add_argument("--event-fd", type=int)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    if args.action == "_session":
        if args.event_fd is None or args.parent_pid is None:
            parser.error("internal session requires parent and event pipe")
    elif args.event_fd is not None or args.parent_pid is not None:
        parser.error("event pipe is only accepted for internal sessions")
    signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        if args.action == "_session":
            return worker(args.source, args.target, args.rtsp, args.event_fd, args.parent_pid)
        with RollingBudget(args.state_dir, args.source, args.target) as budget:
            if args.action == "init":
                budget.initialize()
                print("continuous: budget initialized", flush=True)
                return 0
            return supervise_continuous(budget, args.rtsp)
    except KeyboardInterrupt:
        print("continuous: STOPPED; active attempt remains in_progress", flush=True)
        return 130
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"continuous: error={type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
