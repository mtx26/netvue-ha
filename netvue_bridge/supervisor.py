import argparse
import fcntl
import ipaddress
import json
import math
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from .rtsp import validate_local_rtsp_url
from .stream import run_sessions


class Budget:
    def __init__(self, directory: Path, source: str, target: str) -> None:
        self.directory = directory
        self.source = str(ipaddress.IPv4Address(source))
        self.target = str(ipaddress.IPv4Address(target))
        if self.source == self.target:
            raise ValueError("Source and target must differ")
        self.path = directory / "budget.json"
        self.lock_file = None

    def __enter__(self) -> "Budget":
        if not self.directory.is_dir():
            raise ValueError("Budget directory must already exist")
        fd = os.open(self.directory / "budget.lock",
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.lock_file = os.fdopen(fd, "r+b")
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            self.lock_file = None
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self.lock_file is not None:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            self.lock_file.close()
            self.lock_file = None

    def _sync_directory(self) -> None:
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def initialize(self, limit: int) -> None:
        if self.lock_file is None:
            raise RuntimeError("Budget lock is required")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Budget limit must be between 1 and 1000")
        data = {"version": 2, "source": self.source, "target": self.target,
                "limit": limit, "used": 0, "status": "ready"}
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file)
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
            raise ValueError("Budget file is too large")
        try:
            state = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Budget file is invalid") from exc
        if (not isinstance(state, dict)
                or set(state) != {"version", "source", "target", "limit", "used", "status"}
                or type(state["version"]) is not int or state["version"] != 2
                or state["source"] != self.source or state["target"] != self.target
                or type(state["limit"]) is not int or not 1 <= state["limit"] <= 1000
                or type(state["used"]) is not int or not 0 <= state["used"] <= state["limit"]
                or state["status"] not in ("ready", "in_progress", "blocked")
                or (state["status"] != "ready" and state["used"] == 0)):
            raise ValueError("Budget state or endpoint is invalid")
        return state

    def reserve(self) -> int:
        state = self.read()
        if state["status"] != "ready":
            raise ValueError("Budget is blocked by an unfinished or terminal attempt")
        if state["used"] >= state["limit"]:
            raise ValueError("Connection budget exhausted")
        state["used"] += 1
        state["status"] = "in_progress"
        self._write(state)
        return state["used"]

    def finish(self, retryable: bool) -> None:
        state = self.read()
        if state["status"] != "in_progress":
            raise ValueError("No attempt in progress")
        state["status"] = "ready" if retryable else "blocked"
        self._write(state)

    def _write(self, state: dict) -> None:
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".budget-", delete=False) as file:
                name = file.name
                json.dump(state, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.path)
            name = None
            self._sync_directory()
        finally:
            if name is not None:
                os.unlink(name)


def retry_delay(used: int) -> int:
    if used < 1:
        raise ValueError("Retry delay requires a previous attempt")
    return min(60, 5 * (2 ** min(used - 1, 4)))


def wait_for_rtsp(port: int, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print("supervisor: RTSP listener unavailable", flush=True)
                return False
            time.sleep(min(0.5, remaining))


def supervise(budget: Budget, rtsp: str) -> int:
    validate_local_rtsp_url(rtsp)
    port = urlsplit(rtsp).port
    if port is None:
        raise ValueError("RTSP port required")
    while True:
        state = budget.read()
        if state["status"] == "in_progress":
            print("supervisor: BUDGET_IN_PROGRESS; manual review required", flush=True)
            return 1
        if state["status"] == "blocked":
            print("supervisor: BUDGET_BLOCKED; manual review required", flush=True)
            return 1
        used, limit = state["used"], state["limit"]
        if used >= limit:
            print("supervisor: BUDGET_EXHAUSTED; connection budget exhausted", flush=True)
            return 1
        if used:
            delay = retry_delay(used)
            print(f"supervisor: waiting {delay}s before next attempt", flush=True)
            time.sleep(delay)
        if not wait_for_rtsp(port):
            return 1
        attempt = budget.reserve()
        print(f"supervisor: reserved attempt {attempt} of {limit}", flush=True)
        retryable = []
        result = run_sessions(budget.source, budget.target, rtsp, float("inf"),
                              max_attempts=1, on_retryable=retryable.append)
        budget.finish(retryable=bool(retryable) and result != 0)
        if result == 0:
            return 0
        if not retryable:
            print("supervisor: non-retryable failure; stopped", flush=True)
            return result
        print(f"supervisor: retryable interruption at {retryable[-1]}", flush=True)


def run_bounded(command: list[str], duration: float) -> int:
    if not math.isfinite(duration) or not 0 < duration <= 604800:
        raise ValueError("Maximum duration must be between 0 and 604800 seconds")
    deadline = time.monotonic() + duration
    grace = min(5.0, duration / 4)
    received_signal = None

    def request_stop(signum, frame) -> None:
        nonlocal received_signal
        received_signal = signum

    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    child = None
    try:
        child = subprocess.Popen(command)
        while child.poll() is None:
            remaining = deadline - grace - time.monotonic()
            if received_signal is not None or remaining <= 0:
                timed_out = received_signal is None
                stop_code = 124 if received_signal is None else 128 + received_signal
                print("watchdog: duration limit reached" if timed_out
                      else "watchdog: stop requested", flush=True)
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    print("watchdog: forcing project worker to stop", flush=True)
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
                    child.wait()
                return stop_code
            try:
                child.wait(timeout=min(remaining, 0.1))
            except subprocess.TimeoutExpired:
                pass
        return child.returncode if child.returncode >= 0 else 128 - child.returncode
    finally:
        if child is not None and child.poll() is None:
            try:
                child.kill()
            except ProcessLookupError:
                pass
            child.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent, bounded Netvue session supervisor")
    parser.add_argument("action", choices=("init", "run", "_worker"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--state-dir", type=Path, default=Path("/state"))
    parser.add_argument("--rtsp", default="rtsp://127.0.0.1:8554/netvue2")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-duration", type=float)
    args = parser.parse_args()
    if args.action == "init" and args.limit is None:
        parser.error("init requires --limit")
    if args.action != "init" and args.limit is not None:
        parser.error("--limit is only accepted for init")
    if args.action == "run":
        if args.max_duration is None or not math.isfinite(args.max_duration) \
                or not 0 < args.max_duration <= 604800:
            parser.error("run requires --max-duration between 0 and 604800 seconds")
    elif args.max_duration is not None:
        parser.error("--max-duration is only accepted for run")

    def stop(signum, frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        if args.action == "run":
            print("supervisor: starting bounded worker", flush=True)
            command = [sys.executable, "-B", "-m", "netvue_bridge.supervisor", "_worker",
                       "--source", args.source, "--target", args.target,
                       "--state-dir", str(args.state_dir), "--rtsp", args.rtsp]
            return run_bounded(command, args.max_duration)
        with Budget(args.state_dir, args.source, args.target) as budget:
            if args.action == "init":
                budget.initialize(args.limit)
                print("supervisor: budget initialized", flush=True)
                return 0
            return supervise(budget, args.rtsp)
    except KeyboardInterrupt:
        print("supervisor: stopped", flush=True)
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"supervisor: error={type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
