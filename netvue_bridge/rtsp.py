import re
import time
from collections import deque
from fractions import Fraction
from statistics import median
from urllib.parse import urlsplit

from .protocol import ProtocolError


START_CODE = re.compile(b"\x00\x00(?:\x00)?\x01")
MAX_STARTUP_BYTES = 4 * 1024 * 1024
MAX_STARTUP_FRAMES = 128
MAX_IDR_WAIT_SECONDS = 5
MAX_DECODE_ERRORS = 3
MAX_COLLISION_FRAMES = 8
MAX_COLLISION_BYTES = 4 * 1024 * 1024
MAX_COLLISION_WAIT = 2.0


def validate_local_rtsp_url(url: str) -> None:
    parts = urlsplit(url)
    if (parts.scheme != "rtsp" or parts.hostname != "127.0.0.1"
            or parts.username is not None or parts.password is not None
            or parts.port is None or not parts.path or parts.path == "/"
            or parts.query or parts.fragment):
        raise ValueError("RTSP output must be a loopback URL with an explicit port and path")


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


class TimestampClock:
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


class H264Publisher:
    def __init__(self, url: str) -> None:
        validate_local_rtsp_url(url)
        try:
            import av
        except ModuleNotFoundError as exc:
            if exc.name != "av":
                raise
            raise ProtocolError("PyAV is required for RTSP publication") from exc

        self.av = av
        self.url = url
        self.decoder = av.CodecContext.create("h264", "r")
        self.sps: bytes | None = None
        self.pps: bytes | None = None
        self.started = False
        self.clock = TimestampClock()
        self.last_clock_time: float | None = None
        self.live_reconfirmed = False
        self.timestamp_resets = 0
        self.last_reset_gap_ms: int | None = None
        self.pending: list[tuple[bytes, int, bool]] = []
        self.pending_bytes = 0
        self.output = None
        self.stream = None
        self.width: int | None = None
        self.height: int | None = None
        self.config_changed = False
        self.change_decoder = None
        self.change_pending: list[tuple[bytes, int, bool]] = []
        self.change_pending_bytes = 0
        self.waiting_since = time.monotonic()
        self.consecutive_decode_errors = 0
        self.parameter_updates = 0
        self.sps_changes = 0
        self.pps_changes = 0
        self.resolution_changes = 0
        self.decode_errors = 0
        self.video_packets = 0
        self.input_key_frames = 0
        self.first_video_time: float | None = None
        self.last_video_time: float | None = None
        self.first_video_pts: int | None = None
        self.last_video_pts: int | None = None
        self.published_frames = 0
        self.key_frames = 0
        self.same_timestamps = 0
        self.timestamp_corrections = 0
        self.ignored_frames = 0
        self._last_access_unit: bytes | None = None
        self._collision_frames: list[tuple[bytes, float]] = []
        self._collision_bytes = 0
        self._collision_started: float | None = None

    def push(self, data: bytes, timestamp: int) -> None:
        now = time.monotonic()
        self.video_packets += 1
        if self.first_video_time is None:
            self.first_video_time = now
            self.first_video_pts = timestamp
        self.last_video_time = now
        self.last_video_pts = timestamp
        units = annexb_nals(data)
        types = {nal[0] & 31 for nal in units}
        self.input_key_frames += int(5 in types)
        has_picture = bool(types.intersection({1, 5}))
        if self._collision_frames and (
                self._collision_started is None
                or now - self._collision_started > MAX_COLLISION_WAIT):
            raise ProtocolError("Duplicate timestamp resolution timed out")
        if not has_picture:
            if self._collision_frames:
                raise ProtocolError("Non-picture packet during duplicate timestamp resolution")
            self._push_frame(data, timestamp, now)
            return

        if self._collision_frames or (
                self._last_access_unit is not None and timestamp == self.clock.previous):
            if timestamp == self.clock.previous:
                self.same_timestamps += 1
            if self.config_changed or self.live_reconfirmed or any(
                (nal[0] & 31) in (7, 8)
                and nal.rstrip(b"\x00") != (self.sps if (nal[0] & 31) == 7 else self.pps)
                for nal in units
            ):
                raise ProtocolError("Duplicate timestamp overlaps a parameter change or LIVE confirmation")
            previous = self._collision_frames[-1][0] if self._collision_frames else self._last_access_unit
            if previous is None:
                raise ProtocolError("Previous H.264 access unit unavailable")
            self._verify_picture_boundary(previous, data)
            if timestamp == self.clock.previous:
                if (len(self._collision_frames) >= MAX_COLLISION_FRAMES
                        or self._collision_bytes + len(data) > MAX_COLLISION_BYTES):
                    raise ProtocolError("Duplicate timestamp buffer limit exceeded")
                if not self._collision_frames:
                    self._collision_started = now
                self._collision_frames.append((data, now))
                self._collision_bytes += len(data)
                return
            self._resolve_collisions(data, timestamp, now)
            return

        before = self.clock.elapsed
        previous_timestamp = self.clock.previous
        self._push_frame(data, timestamp, now)
        if (self.clock.previous == timestamp
                and (previous_timestamp is None or self.clock.elapsed != before)):
            self._last_access_unit = data if len(data) <= MAX_COLLISION_BYTES else None

    def _verify_picture_boundary(self, previous: bytes, current: bytes) -> None:
        if previous == current:
            raise ProtocolError("Identical H.264 payloads make timestamp resolution ambiguous")
        if self.sps is None or self.pps is None:
            raise ProtocolError("H.264 parameters unavailable for timestamp resolution")
        for data in (previous, current):
            slices = [nal for nal in annexb_nals(data) if (nal[0] & 31) in (1, 5)]
            if not slices or first_slice_macroblock(slices[0]) != 0:
                raise ProtocolError("Ambiguous H.264 picture boundary for duplicate timestamp")
        prefix = b"\x00\x00\x00\x01" + self.sps + b"\x00\x00\x00\x01" + self.pps
        parser = self.av.CodecContext.create("h264", "r")
        try:
            packets = parser.parse(prefix + previous)
            packets.extend(parser.parse(current))
            packets.extend(parser.parse(None))
        except self.av.error.FFmpegError as exc:
            raise ProtocolError("Cannot verify H.264 picture boundary") from exc
        if [bytes(packet) for packet in packets] != [prefix + previous, current]:
            raise ProtocolError("Ambiguous H.264 picture boundary for duplicate timestamp")

    def _resolve_collisions(self, next_data: bytes, next_timestamp: int, now: float) -> None:
        previous = self.clock.previous
        if previous is None or len(self.clock.intervals) < 3:
            raise ProtocolError("Insufficient cadence history for duplicate timestamp")
        gap = (next_timestamp - previous) & 0xFFFFFFFF
        count = len(self._collision_frames) + 1
        expected = median(self.clock.intervals)
        if (gap > 10000 or gap >= 0x80000000 or gap < count
                or not expected * 0.5 <= gap / count <= expected * 1.5):
            raise ProtocolError("Duplicate timestamp cannot be safely interpolated")
        for index, (frame, arrival) in enumerate(self._collision_frames, 1):
            interpolated = (previous + gap * index // count) & 0xFFFFFFFF
            self._push_frame(frame, interpolated, arrival)
            self.timestamp_corrections += 1
        self._collision_frames.clear()
        self._collision_bytes = 0
        self._collision_started = None
        self._push_frame(next_data, next_timestamp, now)
        self._last_access_unit = next_data if len(next_data) <= MAX_COLLISION_BYTES else None

    def _push_frame(self, data: bytes, timestamp: int, now: float) -> None:
        units = annexb_nals(data)
        types = {nal[0] & 31 for nal in units}
        for nal in units:
            kind = nal[0] & 31
            if kind in (7, 8):
                nal = nal.rstrip(b"\x00")
                current = self.sps if kind == 7 else self.pps
                if self.started and current is not None and current != nal:
                    if kind == 7:
                        self.sps_changes += 1
                    else:
                        self.pps_changes += 1
                    if self.output is None:
                        self.started = False
                        self.decoder = self.av.CodecContext.create("h264", "r")
                        self.ignored_frames += len(self.pending)
                        self.pending.clear()
                        self.pending_bytes = 0
                        self.clock = TimestampClock()
                        self.last_clock_time = None
                        self.waiting_since = now
                    elif not self.config_changed or self.change_decoder is not None:
                        self.waiting_since = now
                    if self.started:
                        self.config_changed = True
                        self.change_decoder = None
                        self.ignored_frames += len(self.change_pending)
                        self.change_pending.clear()
                        self.change_pending_bytes = 0
                if kind == 7:
                    self.sps = nal
                else:
                    self.pps = nal
        if (self.output is None or self.config_changed) and now - self.waiting_since > MAX_IDR_WAIT_SECONDS:
            if self.sps is None:
                raise ProtocolError("H.264 SPS missing before startup")
            if self.pps is None:
                raise ProtocolError("H.264 PPS missing before startup")
            if not self.started or (self.config_changed and self.change_decoder is None):
                raise ProtocolError("H.264 IDR missing within 5 seconds")
            raise ProtocolError("H.264 decodable image missing within 5 seconds")
        if not types.intersection({1, 5}):
            return
        key = 5 in types
        if not self.started:
            if not key or self.sps is None or self.pps is None:
                self.ignored_frames += 1
                return
            self.started = True
            data = b"\x00\x00\x00\x01" + self.sps + b"\x00\x00\x00\x01" + self.pps + data
        elif self.config_changed:
            if self.sps is None or self.pps is None:
                raise ProtocolError("Changed H.264 parameters are incomplete")
            if self.change_decoder is None:
                if not key:
                    self.ignored_frames += 1
                    return
                self.change_decoder = self.av.CodecContext.create("h264", "r")
                data = b"\x00\x00\x00\x01" + self.sps + b"\x00\x00\x00\x01" + self.pps + data
            pts = self._timestamp(timestamp, now, key)
            self.change_pending_bytes += len(data)
            if (self.change_pending_bytes > MAX_STARTUP_BYTES
                    or len(self.change_pending) >= MAX_STARTUP_FRAMES):
                raise ProtocolError("Changed H.264 parameters exceeded the buffer limit")
            self.change_pending.append((data, pts, key))
            try:
                frames = self.change_decoder.decode(self.av.Packet(data))
            except self.av.error.FFmpegError as exc:
                self.decode_errors += 1
                self.consecutive_decode_errors += 1
                if self.consecutive_decode_errors >= MAX_DECODE_ERRORS:
                    raise ProtocolError("Repeated decode errors after H.264 parameter change") from exc
                print("live: decoder error after parameter change; waiting for IDR", flush=True)
                self.change_decoder = None
                self.ignored_frames += len(self.change_pending)
                self.change_pending.clear()
                self.change_pending_bytes = 0
                self.waiting_since = now
                return
            if self.change_decoder.has_b_frames:
                raise ProtocolError("Changed H.264 parameters require B-frame reordering")
            if not frames:
                return
            if (frames[0].width, frames[0].height) != (self.width, self.height):
                self.resolution_changes += 1
            self.width, self.height = frames[0].width, frames[0].height
            self.config_changed = False
            self.live_reconfirmed = False
            self.parameter_updates += 1
            self.consecutive_decode_errors = 0
            self.change_decoder = None
            for pending_data, pending_pts, pending_key in self.change_pending:
                self._mux(pending_data, pending_pts, pending_key)
            self.change_pending.clear()
            self.change_pending_bytes = 0
            return
        pts = self._timestamp(timestamp, now, key)
        if self.output is None:
            self.pending_bytes += len(data)
            if self.pending_bytes > MAX_STARTUP_BYTES or len(self.pending) >= MAX_STARTUP_FRAMES:
                raise ProtocolError("H.264 startup exceeded the buffer limit")
            self.pending.append((data, pts, key))
            try:
                frames = self.decoder.decode(self.av.Packet(data))
            except self.av.error.FFmpegError as exc:
                self.decode_errors += 1
                raise ProtocolError("Unable to decode initial H.264 parameters") from exc
            if self.decoder.has_b_frames:
                raise ProtocolError("H.264 B-frame reordering is not supported")
            if not frames:
                return
            frame = frames[0]
            self.width, self.height = frame.width, frame.height
            try:
                self.output = self.av.open(
                    self.url, mode="w", format="rtsp",
                    options={"rtsp_transport": "tcp"}, timeout=3.0,
                )
                self.stream = self.output.add_stream("h264", rate=10)
                self.stream.width = frame.width
                self.stream.height = frame.height
                self.stream.codec_context.time_base = Fraction(1, 1000)
                self.stream.codec_context.extradata = (
                    b"\x00\x00\x00\x01" + self.sps + b"\x00\x00\x00\x01" + self.pps
                )
            except self.av.error.FFmpegError as exc:
                raise ProtocolError("Unable to open the RTSP publisher") from exc
            for pending_data, pending_pts, pending_key in self.pending:
                self._mux(pending_data, pending_pts, pending_key)
            self.pending.clear()
            self.pending_bytes = 0
        else:
            self._mux(data, pts, key)

    def note_repeated_live_confirmation(self) -> None:
        self.live_reconfirmed = True

    def _timestamp(self, timestamp: int, now: float, key: bool) -> int:
        previous = self.clock.previous
        if (self.config_changed and key and self.live_reconfirmed
                and timestamp == 0 and previous is not None
                and ((timestamp - previous) & 0xFFFFFFFF) > 0x80000000):
            if self.last_clock_time is None:
                raise ProtocolError("Timestamp reset has no previous arrival time")
            gap_ms = round((now - self.last_clock_time) * 1000)
            pts = self.clock.rebase_zero(gap_ms)
            self.timestamp_resets += 1
            self.last_reset_gap_ms = gap_ms
            self.live_reconfirmed = False
            print("live: PTS reset rebased using arrival time", flush=True)
        else:
            pts = self.clock.update(timestamp)
        self.last_clock_time = now
        return pts

    def _mux(self, data: bytes, pts: int, key: bool) -> None:
        packet = self.av.Packet(data)
        packet.pts = packet.dts = pts
        packet.time_base = Fraction(1, 1000)
        packet.stream = self.stream
        packet.is_keyframe = key
        try:
            self.output.mux(packet)
        except self.av.error.FFmpegError as exc:
            raise ProtocolError("RTSP publication failed") from exc
        self.published_frames += 1
        self.key_frames += int(key)

    def safe_metrics(self) -> dict[str, int | float | None]:
        span = None
        if self.first_video_time is not None and self.last_video_time is not None:
            span = round(self.last_video_time - self.first_video_time, 3)
        return {
            "video_packets": self.video_packets,
            "input_idr": self.input_key_frames,
            "published_frames": self.published_frames,
            "published_idr": self.key_frames,
            "sps_changes": self.sps_changes,
            "pps_changes": self.pps_changes,
            "resolution_changes": self.resolution_changes,
            "parameter_updates": self.parameter_updates,
            "decode_errors": self.decode_errors,
            "timestamp_resets": self.timestamp_resets,
            "same_timestamps": self.same_timestamps,
            "timestamp_corrections": self.timestamp_corrections,
            "ignored_frames": self.ignored_frames,
            "unresolved_timestamp_frames": len(self._collision_frames),
            "max_frame_gap_ms": self.clock.max_gap_ms,
            "estimated_fps": (round((self.published_frames - 1) * 1000 / self.clock.elapsed, 2)
                              if self.published_frames > 1 and self.clock.elapsed else None),
            "last_valid_timestamp": self.clock.previous,
            "last_reset_gap_ms": self.last_reset_gap_ms,
            "video_wall_span_seconds": span,
            "first_video_pts": self.first_video_pts,
            "last_video_pts": self.last_video_pts,
            "video_pts_span_ms": self.clock.elapsed,
        }

    def close(self) -> None:
        if self.output is not None:
            output, self.output = self.output, None
            output.close()
        self.pending.clear()
        self.pending_bytes = 0
        self.change_pending.clear()
        self.change_pending_bytes = 0
        self.change_decoder = None
        self._last_access_unit = None
        self._collision_frames.clear()
        self._collision_bytes = 0
        self._collision_started = None
