"""Unified Netvue session supervisor with a persistent, retry-only budget."""

import argparse
import ctypes
import json
import math
import os
import select
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import budget as budget_module
from .budget import Budget, DEFAULT_RETRY_DELAYS, parse_retry_delays, retry_delay
from .rtsp import validate_local_rtsp_url
from .stream import run_session

HEARTBEAT_TIMEOUT = 30


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


def active_session_pids(target: str, proc_root: Path = Path("/proc")) -> list[int]:
    modules = {
        b"netvue_bridge.continuous": {b"run", b"_run", b"_session"},
    }
    target_arg = str(target).encode("ascii")
    active = []
    for entry in proc_root.iterdir():
        if not entry.name.isdecimal():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        try:
            with (entry / "cmdline").open("rb") as command_line:
                raw = command_line.read(65537)
        except FileNotFoundError:
            continue
        if len(raw) > 65536:
            raise ValueError(f"Cannot safely inspect process {pid} command line")
        args = raw.rstrip(b"\0").split(b"\0")
        try:
            module_index = args.index(b"-m")
            module = args[module_index + 1]
        except (ValueError, IndexError):
            continue
        actions = modules.get(module)
        if (actions is None or module_index + 2 >= len(args)
                or args[module_index + 2] not in actions):
            continue
        if any(arg == b"--target" and index + 1 < len(args)
               and args[index + 1] == target_arg
               for index, arg in enumerate(args)):
            active.append(pid)
    return sorted(active)


def require_no_active_session(target: str) -> None:
    pids = active_session_pids(target)
    if pids:
        raise RuntimeError("ACTIVE_SESSION; refusing another session")


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


def worker(source: str, target: str, rtsp: str, fd: int, parent: int,
           video_timeout: float = 30) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot configure parent death signal")
    if os.getppid() != parent:
        raise RuntimeError("Supervisor disappeared before worker startup")
    signal.signal(signal.SIGTERM, signal.default_int_handler)
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
        outcome = run_session(source, target, rtsp, float("inf"),
                              on_stage=stage, on_video=video,
                              video_timeout=video_timeout)
        if outcome is None:
            return 0
        if outcome == "blocked":
            send({"event": "blocked"})
        elif outcome in ("discovery", "connect", "live", "video"):
            send({"event": "retryable", "stage": outcome})
        else:
            raise RuntimeError("Unknown session outcome")
        return 1
    finally:
        os.close(fd)


def run_worker(budget: Budget, rtsp: str, video_timeout: float | None = None) -> str:
    if video_timeout is None:
        video_timeout = HEARTBEAT_TIMEOUT
    if not math.isfinite(video_timeout) or video_timeout <= 0:
        raise ValueError("Video timeout must be positive and finite")
    read_fd, write_fd = os.pipe()
    command = [sys.executable, "-B", "-m", "netvue_bridge.continuous", "_session",
               "--source", budget.source, "--target", budget.target, "--rtsp", rtsp,
               "--event-fd", str(write_fd), "--parent-pid", str(os.getpid()),
               "--video-timeout", str(video_timeout)]
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
        waiting_video = False
        stable = False
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
                            if event["stage"] == "live":
                                waiting_video = True
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
                                if not stable and metrics.get("session_seconds", 0) >= 300:
                                    budget.mark_stable()
                                    stable = True
                                    print("continuous: stable session; retry streak cleared",
                                          flush=True)
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
            if now - last_progress > (video_timeout if waiting_video else HEARTBEAT_TIMEOUT):
                print("continuous: WATCHDOG_TIMEOUT; " +
                      ("video stalled, stopping worker" if waiting_video
                       else "worker stalled, manual review required"), flush=True)
                return "live" if waiting_video else "ambiguous"
            if metrics and now - last_publication > video_timeout:
                print("continuous: PUBLICATION_STALLED; stopping worker", flush=True)
                return "live"
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
        if process.returncode == 1 and outcome in ("discovery", "connect", "live", "video"):
            print("continuous: retryable interruption at " + outcome, flush=True)
            return outcome
        if process.returncode == 1 and outcome == "blocked":
            print("continuous: non-retryable session failure", flush=True)
            return "blocked"
        print(f"continuous: unexpected worker exit={process.returncode}", flush=True)
        return "ambiguous"
    finally:
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("continuous: forcing project worker to stop", flush=True)
                process.kill()
                process.wait()
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def supervise(budget: Budget, rtsp: str, video_timeout: float = 30,
              max_attempts: int | None = None) -> int:
    if not math.isfinite(video_timeout) or video_timeout <= 0:
        raise ValueError("Video timeout must be positive and finite")
    if max_attempts is not None and (type(max_attempts) is not int or max_attempts < 1):
        raise ValueError("Maximum attempts must be positive")
    validate_local_rtsp_url(rtsp)
    port = urlsplit(rtsp).port
    if port is None:
        raise ValueError("RTSP port required")
    state = budget.read()
    if state["status"] == "blocked":
        print("continuous: BUDGET_BLOCKED", flush=True)
        return 1
    _, _, boot_id = budget_module.clock_sample()
    if state["status"] == "in_progress" or state["boot_id"] != boot_id:
        require_no_active_session(budget.target)
        if budget.recover_after_restart():
            print("continuous: recovered interrupted state after restart", flush=True)
    attempts = 0
    while True:
        require_no_active_session(budget.target)
        state, wall, boot = budget.checked()
        if state["status"] != "ready":
            print("continuous: BUDGET_" + state["status"].upper(), flush=True)
            return 1
        remaining = state["wait_until"] - boot
        if remaining > 0:
            if max_attempts is not None and attempts >= max_attempts:
                return 0
            print(f"continuous: state=RECONNECT_WAIT remaining={remaining:.1f}s", flush=True)
            time.sleep(remaining)
            continue
        if not wait_for_rtsp(port):
            delay = retry_delay(max(1, state["streak"] + 1),
                                budget.retry_delays, budget.retry_jitter)
            print(f"continuous: RTSP_UNAVAILABLE; retry in {delay:.1f}s", flush=True)
            time.sleep(delay)
            continue
        count = budget.reserve()
        attempts += 1
        print(f"continuous: reserved attempt {count}; "
              f"reconnections={max(0, count - 1)}", flush=True)
        result = run_worker(budget, rtsp, video_timeout)
        if result in ("discovery", "connect", "live", "video"):
            budget.finish_retryable(result)
            if max_attempts is not None and attempts >= max_attempts:
                return 0
        elif result == "blocked":
            budget.block()
            return 1
        else:
            return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Unified Netvue session supervisor")
    parser.add_argument("action", choices=("init", "run", "test", "_run", "_session"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--state-dir", type=Path, default=Path("/state"))
    parser.add_argument("--rtsp", default="rtsp://127.0.0.1:8554/netvue2")
    parser.add_argument("--event-fd", type=int)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--retry-delays", type=parse_retry_delays, default=DEFAULT_RETRY_DELAYS)
    parser.add_argument("--retry-jitter", type=float, default=0.1)
    parser.add_argument("--video-timeout", type=float, default=30)
    parser.add_argument("--max-duration", type=float)
    parser.add_argument("--max-attempts", type=int)
    args = parser.parse_args()
    if args.action == "_session":
        if args.event_fd is None or args.parent_pid is None:
            parser.error("internal session requires parent and event pipe")
    elif args.event_fd is not None or args.parent_pid is not None:
        parser.error("event pipe is only accepted for internal sessions")
    if not math.isfinite(args.retry_jitter) or not 0 <= args.retry_jitter <= 0.2:
        parser.error("--retry-jitter must be between 0 and 0.2")
    if not math.isfinite(args.video_timeout) or not 1 <= args.video_timeout <= 300:
        parser.error("--video-timeout must be between 1 and 300 seconds")
    if args.action == "test":
        if (args.max_duration is None or not math.isfinite(args.max_duration)
                or not 0 < args.max_duration <= 604800 or args.max_attempts is None
                or args.max_attempts < 1):
            parser.error("test requires --max-duration (1..604800) and --max-attempts")
    elif args.action == "run":
        if args.max_duration is not None or args.max_attempts is not None:
            parser.error("run is continuous; use test for bounded execution")
    elif args.action == "_run":
        if args.max_duration is not None or args.max_attempts is None or args.max_attempts < 1:
            parser.error("internal bounded run requires --max-attempts")
    elif args.max_duration is not None or args.max_attempts is not None:
        parser.error("--max-duration and --max-attempts are only accepted for test")
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        if args.action == "_session":
            return worker(args.source, args.target, args.rtsp, args.event_fd, args.parent_pid,
                          args.video_timeout)
        if args.action == "test":
            command = [sys.executable, "-B", "-m", "netvue_bridge.continuous", "_run",
                       "--source", args.source, "--target", args.target,
                       "--state-dir", str(args.state_dir), "--rtsp", args.rtsp,
                       "--retry-delays", ",".join(map(str, args.retry_delays)),
                       "--retry-jitter", str(args.retry_jitter),
                       "--video-timeout", str(args.video_timeout),
                       "--max-attempts", str(args.max_attempts)]
            return run_bounded(command, args.max_duration)
        with Budget(args.state_dir, args.source, args.target,
                    args.retry_delays, args.retry_jitter) as budget:
            if args.action == "init":
                budget.initialize()
                print("supervisor: state initialized", flush=True)
                return 0
            return supervise(budget, args.rtsp, args.video_timeout,
                             args.max_attempts if args.action == "_run" else None)
    except KeyboardInterrupt:
        print("continuous: STOPPED; active attempt remains in_progress", flush=True)
        return 130
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"continuous: error={type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
