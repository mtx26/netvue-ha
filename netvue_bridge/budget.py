"""Single persistent session state and retry policy for the Netvue bridge."""

import fcntl
import hashlib
import ipaddress
import json
import math
import os
import random
import tempfile
import time
import uuid
from pathlib import Path

DEFAULT_RETRY_DELAYS = (5, 15, 60, 300, 900)
_STAGES = {"READY", "CONNECTING", "AUTHENTICATING", "WAITING_VIDEO",
           "STREAMING", "RECONNECT_WAIT", "BLOCKED"}


def validate_retry_delays(delays: tuple[int, ...]) -> None:
    if (len(delays) != 5 or any(type(delay) is not int or not 1 <= delay <= 900 for delay in delays)
            or tuple(sorted(delays)) != delays):
        raise ValueError("Retry delays must be five nondecreasing seconds between 1 and 900")


def parse_retry_delays(value: str) -> tuple[int, ...]:
    import argparse

    try:
        delays = tuple(int(item) for item in value.split(","))
        validate_retry_delays(delays)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return delays


def retry_delay(used: int, delays: tuple[int, ...] = DEFAULT_RETRY_DELAYS,
                jitter: float = 0.0) -> float:
    if used < 1:
        raise ValueError("Retry delay requires a previous attempt")
    validate_retry_delays(delays)
    if not math.isfinite(jitter) or not 0 <= jitter <= 0.2:
        raise ValueError("Retry jitter must be between 0 and 0.2")
    return min(900.0, delays[min(used, len(delays)) - 1] *
               (1 + random.SystemRandom().uniform(0, jitter)))


def clock_sample() -> tuple[float, float, str]:
    return (time.time(), time.clock_gettime(time.CLOCK_BOOTTIME),
            Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip())


class Budget:
    """One locked, crash-safe state file; retry history never imposes a lifetime quota."""

    def __init__(self, directory: Path, source: str, target: str,
                 retry_delays: tuple[int, ...] = DEFAULT_RETRY_DELAYS,
                 retry_jitter: float = 0.0) -> None:
        self.directory = directory
        self.source = str(ipaddress.IPv4Address(source))
        self.target = str(ipaddress.IPv4Address(target))
        if self.source == self.target:
            raise ValueError("Source and target must differ")
        validate_retry_delays(retry_delays)
        if not math.isfinite(retry_jitter) or not 0 <= retry_jitter <= 0.2:
            raise ValueError("Retry jitter must be between 0 and 0.2")
        self.retry_delays = retry_delays
        self.retry_jitter = retry_jitter
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

    def _require_lock(self) -> None:
        if self.lock_file is None:
            raise RuntimeError("Budget lock is required")

    def _sync_directory(self) -> None:
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write(self, state: dict) -> None:
        self._require_lock()
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                             prefix=".budget-", delete=False) as file:
                name = file.name
                json.dump(state, file, sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.path)
            name = None
            self._sync_directory()
        finally:
            if name is not None:
                os.unlink(name)

    def _read_raw(self) -> tuple[bytes, dict]:
        self._require_lock()
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as file:
            raw = file.read(4097)
        if len(raw) > 4096:
            raise ValueError("Budget file is too large")
        try:
            state = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Budget file is invalid") from exc
        if not isinstance(state, dict):
            raise ValueError("Budget state is invalid")
        return raw, state

    def initialize(self) -> None:
        self._require_lock()
        wall, boot, boot_id = clock_sample()
        state = {"version": 4, "source": self.source, "target": self.target,
                 "status": "ready", "stage": "READY", "streak": 0, "total": 0,
                 "last_reconnect": None, "last_error": None, "wait_until": 0.0,
                 "wall": wall, "boot": boot, "boot_id": boot_id}
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(state, file, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        self._sync_directory()

    def read(self) -> dict:
        _, state = self._read_raw()
        if state.get("version") != 4:
            raise ValueError("Unsupported budget version; explicit migration required")
        keys = {"version", "source", "target", "status", "stage", "streak", "total",
                "last_reconnect", "last_error", "wait_until", "wall", "boot", "boot_id"}
        number = lambda value: type(value) in (int, float) and math.isfinite(value) and value >= 0
        if (set(state) != keys or type(state["version"]) is not int or state["version"] != 4
                or state["source"] != self.source or state["target"] != self.target
                or state["status"] not in ("ready", "in_progress", "blocked")
                or not isinstance(state["stage"], str) or state["stage"] not in _STAGES
                or type(state["streak"]) is not int or not 0 <= state["streak"] <= 1000000
                or type(state["total"]) is not int or state["total"] < 0
                or (state["last_reconnect"] is not None and not number(state["last_reconnect"]))
                or state["last_error"] not in
                    (None, "discovery", "connect", "live", "video", "legacy_migration")
                or not all(number(state[key]) for key in ("wait_until", "wall", "boot"))
                or state["wait_until"] > state["boot"] + 1800
                or not isinstance(state["boot_id"], str) or not state["boot_id"]
                or (state["status"] == "in_progress" and state["stage"] not in
                    ("CONNECTING", "AUTHENTICATING", "WAITING_VIDEO", "STREAMING"))
                or (state["status"] == "blocked" and state["stage"] != "BLOCKED")
                or (state["status"] == "ready" and state["stage"] not in ("READY", "RECONNECT_WAIT"))
                or (state["stage"] == "READY" and (state["total"] != 0 or state["streak"] != 0))
                or (state["stage"] == "RECONNECT_WAIT" and
                    (state["total"] == 0 or state["streak"] == 0 or state["last_error"] is None))):
            raise ValueError("Budget state or endpoint is invalid")
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
        total = state["total"] + 1
        state.update(total=total, last_reconnect=wall if total > 1 else state["last_reconnect"],
                     status="in_progress", stage="CONNECTING",
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
        if stage not in ("discovery", "connect", "live", "video"):
            raise ValueError("Invalid retryable stage")
        streak = state["streak"] + 1
        state.update(status="ready", stage="RECONNECT_WAIT", streak=streak,
                     last_error=stage, wait_until=boot + retry_delay(
                         streak, self.retry_delays, self.retry_jitter),
                     wall=wall, boot=boot)
        self._write(state)

    def block(self) -> None:
        state, wall, boot = self.checked()
        state.update(status="blocked", stage="BLOCKED", wall=wall, boot=boot)
        self._write(state)

    def mark_stable(self) -> None:
        state, wall, boot = self.checked()
        if state["status"] != "in_progress" or state["stage"] != "STREAMING":
            raise ValueError("No streaming attempt in progress")
        if state["streak"]:
            state.update(streak=0, wall=wall, boot=boot)
            self._write(state)

    def migrate_legacy(self) -> Path:
        """Explicitly migrate v2/v3 state, preserving ambiguity as blocked and auditing bytes."""
        self._require_lock()
        original, state = self._read_raw()
        if type(state.get("version")) is not int or state["version"] not in (2, 3):
            raise ValueError("Only legacy v2/v3 budgets can be migrated")
        if state.get("source") != self.source or state.get("target") != self.target:
            raise ValueError("Legacy budget endpoint is invalid")
        now_wall, now_boot, now_boot_id = clock_sample()
        migrated = {"version": 4, "source": self.source, "target": self.target,
                    "status": "blocked", "stage": "BLOCKED", "streak": 0, "total": 0,
                    "last_reconnect": None, "last_error": "legacy_migration",
                    "wait_until": now_boot, "wall": now_wall, "boot": now_boot,
                    "boot_id": now_boot_id}
        if state["version"] == 2:
            expected = {"version", "source", "target", "limit", "used", "status"}
            if (set(state) != expected or type(state["limit"]) is not int
                    or not 1 <= state["limit"] <= 1000 or type(state["used"]) is not int
                    or not 0 <= state["used"] <= state["limit"]
                    or state["status"] not in ("ready", "in_progress", "blocked")
                    or (state["status"] != "ready" and state["used"] == 0)):
                raise ValueError("Legacy v2 budget is invalid")
            migrated["total"] = state["used"]
        else:
            expected = {"version", "source", "target", "status", "stage", "attempts",
                        "streak", "total", "last_reconnect", "last_error",
                        "wait_until", "wall", "boot", "boot_id"}
            number = lambda value: type(value) in (int, float) and math.isfinite(value) and value >= 0
            if (set(state) != expected or state["status"] not in ("ready", "in_progress", "blocked")
                    or not isinstance(state["stage"], str) or state["stage"] not in _STAGES
                    or not isinstance(state["attempts"], list) or len(state["attempts"]) > 8
                    or not all(number(value) for value in state["attempts"])
                    or state["attempts"] != sorted(state["attempts"])
                    or type(state["streak"]) is not int or state["streak"] < 0
                    or state["streak"] > 1000000
                    or type(state["total"]) is not int or state["total"] < len(state["attempts"])
                    or (state["last_reconnect"] is not None
                        and not number(state["last_reconnect"]))
                    or state["last_error"] not in (None, "discovery", "connect", "live")
                    or not all(number(state[key]) for key in ("wait_until", "wall", "boot"))
                    or state["wait_until"] > state["boot"] + 1800
                    or not isinstance(state["boot_id"], str) or not state["boot_id"]):
                raise ValueError("Legacy v3 budget is invalid")
            if (any(value > state["boot"] for value in state["attempts"])
                    or (state["status"] == "in_progress" and
                        (not state["attempts"] or state["stage"] not in
                         ("CONNECTING", "AUTHENTICATING", "WAITING_VIDEO", "STREAMING")))
                    or (state["status"] == "blocked" and state["stage"] != "BLOCKED")
                    or (state["status"] == "ready" and state["stage"] not in
                        ("READY", "RECONNECT_WAIT"))
                    or (state["stage"] == "READY" and
                        (state["total"] != 0 or state["attempts"] or state["streak"] != 0))
                    or (state["stage"] == "RECONNECT_WAIT" and
                        (state["total"] == 0 or state["streak"] == 0
                         or state["last_error"] is None))):
                raise ValueError("Legacy v3 state is inconsistent")
            migrated["total"] = state["total"]
            if (state["status"] == "ready" and state["stage"] in ("READY", "RECONNECT_WAIT")
                    and state["boot_id"] == now_boot_id and now_boot >= state["boot"]
                    and abs((now_wall - state["wall"]) - (now_boot - state["boot"])) <= 5):
                migrated.update(status="ready", stage=state["stage"],
                                streak=state["streak"], last_reconnect=state["last_reconnect"],
                                last_error=state["last_error"],
                                wait_until=max(now_boot, state["wait_until"]))
        audit = {"version": 1, "action": "migrate_legacy_budget",
                 "original_sha256": hashlib.sha256(original).hexdigest(),
                 "original_json": original.decode("utf-8"), "after": migrated}
        audit_bytes = json.dumps(audit, sort_keys=True).encode("utf-8")
        audit_path = self.directory / ("migration-" + uuid.uuid4().hex + ".json")
        fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(audit_bytes)
            file.flush()
            os.fsync(file.fileno())
        self._sync_directory()
        fd = os.open(audit_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as file:
            if file.read() != audit_bytes:
                raise OSError("Migration audit verification failed")
        self._write(migrated)
        if self.read() != migrated:
            raise OSError("Migrated budget verification failed")
        return audit_path
