"""H.264 access-unit parsing and the Netvue video timestamp clock."""

import re
from collections import deque
from statistics import median

from .protocol import ProtocolError


START_CODE = re.compile(b"\x00\x00(?:\x00)?\x01")


def annexb_nals(data: bytes) -> list[bytes]:
    starts = list(START_CODE.finditer(data))
    if not starts or starts[0].start() != 0:
        raise ProtocolError("Video frame is not an Annex B access unit")
    result = []
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(data)
        nal = data[match.end():end]
        if not nal:
            raise ProtocolError("Empty H.264 NAL unit")
        result.append(nal)
    return result


def first_slice_macroblock(nal: bytes) -> int:
    rbsp = bytearray()
    zeros = 0
    for byte in nal[1:]:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        rbsp.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
        if len(rbsp) >= 8:
            break
    bits = "".join(f"{byte:08b}" for byte in rbsp)
    prefix = bits.find("1")
    if prefix < 0 or prefix > 31 or len(bits) < 2 * prefix + 1:
        raise ProtocolError("Invalid H.264 slice header")
    return int(bits[prefix:2 * prefix + 1], 2) - 1


def picture_boundary_prefix(previous: bytes, current: bytes,
                            sps: bytes | None, pps: bytes | None) -> bytes:
    if previous == current:
        raise ProtocolError("Identical H.264 payloads make timestamp resolution ambiguous")
    if sps is None or pps is None:
        raise ProtocolError("H.264 parameters unavailable for timestamp resolution")
    for data in (previous, current):
        slices = [nal for nal in annexb_nals(data) if (nal[0] & 31) in (1, 5)]
        if not slices or first_slice_macroblock(slices[0]) != 0:
            raise ProtocolError("Ambiguous H.264 picture boundary for duplicate timestamp")
    return b"\x00\x00\x00\x01" + sps + b"\x00\x00\x00\x01" + pps


def interpolate_duplicate_timestamps(previous: int | None, next_timestamp: int,
                                     buffered: int, intervals: deque[int]) -> list[int]:
    if previous is None or len(intervals) < 3:
        raise ProtocolError("Insufficient cadence history for duplicate timestamp")
    gap = (next_timestamp - previous) & 0xFFFFFFFF
    count = buffered + 1
    expected = median(intervals)
    if (gap > 10000 or gap >= 0x80000000 or gap < count
            or not expected * 0.5 <= gap / count <= expected * 1.5):
        raise ProtocolError("Duplicate timestamp cannot be safely interpolated")
    return [(previous + gap * index // count) & 0xFFFFFFFF
            for index in range(1, count)]


class TimestampClock:
    """Track modulo-32-bit millisecond timestamps, rejecting ambiguous jumps."""

    def __init__(self) -> None:
        self.previous: int | None = None
        self.elapsed = 0
        self.intervals: deque[int] = deque(maxlen=8)
        self.max_gap_ms = 0

    def update(self, value: int) -> int:
        if self.previous is not None:
            delta = (value - self.previous) & 0xFFFFFFFF
            if delta == 0:
                raise ProtocolError("Duplicate video timestamp")
            if delta > 0x80000000:
                raise ProtocolError("Video timestamp moved backwards")
            if delta > 10000:
                raise ProtocolError("Video timestamp discontinuity exceeded 10 seconds")
            self.elapsed += delta
            self.intervals.append(delta)
            self.max_gap_ms = max(self.max_gap_ms, delta)
        self.previous = value
        return self.elapsed

    def rebase_zero(self, gap_ms: int) -> int:
        if self.previous is None or self.previous == 0 or not 0 < gap_ms <= 10000:
            raise ProtocolError("Invalid timestamp reset or arrival-time gap")
        self.elapsed += gap_ms
        self.max_gap_ms = max(self.max_gap_ms, gap_ms)
        self.intervals.clear()
        self.previous = 0
        return self.elapsed
