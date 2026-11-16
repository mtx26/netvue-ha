import re
import time
from fractions import Fraction
from urllib.parse import urlsplit

from .protocol import ProtocolError


START_CODE = re.compile(b"\x00\x00(?:\x00)?\x01")
MAX_STARTUP_BYTES = 4 * 1024 * 1024
MAX_STARTUP_FRAMES = 128
MAX_IDR_WAIT_SECONDS = 5
MAX_DECODE_ERRORS = 3


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


class TimestampClock:
    def __init__(self) -> None:
        self.previous: int | None = None
        self.elapsed = 0

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
        self.previous = value
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
        self.published_frames = 0
        self.key_frames = 0

    def push(self, data: bytes, timestamp: int) -> None:
        now = time.monotonic()
        self.video_packets += 1
        if self.first_video_time is None:
            self.first_video_time = now
        self.last_video_time = now
        units = annexb_nals(data)
        types = {nal[0] & 31 for nal in units}
        self.input_key_frames += int(5 in types)
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
                        self.pending.clear()
                        self.pending_bytes = 0
                        self.clock = TimestampClock()
                        self.waiting_since = now
                    elif not self.config_changed or self.change_decoder is not None:
                        self.waiting_since = now
                    if self.started:
                        self.config_changed = True
                        self.change_decoder = None
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
                return
            self.started = True
            data = b"\x00\x00\x00\x01" + self.sps + b"\x00\x00\x00\x01" + self.pps + data
        elif self.config_changed:
            if self.sps is None or self.pps is None:
                raise ProtocolError("Changed H.264 parameters are incomplete")
            if self.change_decoder is None:
                if not key:
                    return
                self.change_decoder = self.av.CodecContext.create("h264", "r")
                data = b"\x00\x00\x00\x01" + self.sps + b"\x00\x00\x00\x01" + self.pps + data
            pts = self.clock.update(timestamp)
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
            self.parameter_updates += 1
            self.consecutive_decode_errors = 0
            self.change_decoder = None
            for pending_data, pending_pts, pending_key in self.change_pending:
                self._mux(pending_data, pending_pts, pending_key)
            self.change_pending.clear()
            self.change_pending_bytes = 0
            return
        pts = self.clock.update(timestamp)
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
            "video_wall_span_seconds": span,
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
