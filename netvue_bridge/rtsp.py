import re
from fractions import Fraction
from urllib.parse import urlsplit

from .protocol import ProtocolError


START_CODE = re.compile(b"\x00\x00(?:\x00)?\x01")
MAX_STARTUP_BYTES = 4 * 1024 * 1024
MAX_STARTUP_FRAMES = 128


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
            if not 0 < delta <= 10000:
                raise ProtocolError("Non-monotonic or discontinuous video timestamp")
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
        self.published_frames = 0
        self.key_frames = 0

    def push(self, data: bytes, timestamp: int) -> None:
        units = annexb_nals(data)
        types = {nal[0] & 31 for nal in units}
        for nal in units:
            kind = nal[0] & 31
            if kind in (7, 8):
                current = self.sps if kind == 7 else self.pps
                if self.started and current is not None and current != nal:
                    raise ProtocolError("H.264 parameters changed during the session")
                if kind == 7:
                    self.sps = nal
                else:
                    self.pps = nal
        if not types.intersection({1, 5}):
            return
        key = 5 in types
        if not self.started:
            if not key or self.sps is None or self.pps is None:
                return
            self.started = True
            data = b"\x00\x00\x00\x01" + self.sps + b"\x00\x00\x00\x01" + self.pps + data
        pts = self.clock.update(timestamp)
        if self.output is None:
            self.pending_bytes += len(data)
            if self.pending_bytes > MAX_STARTUP_BYTES or len(self.pending) >= MAX_STARTUP_FRAMES:
                raise ProtocolError("H.264 startup exceeded the buffer limit")
            self.pending.append((data, pts, key))
            try:
                frames = self.decoder.decode(self.av.Packet(data))
            except self.av.error.FFmpegError as exc:
                raise ProtocolError("Unable to decode initial H.264 parameters") from exc
            if not frames:
                return
            if self.decoder.has_b_frames:
                raise ProtocolError("H.264 B-frame reordering is not supported")
            frame = frames[0]
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

    def close(self) -> None:
        if self.output is not None:
            output, self.output = self.output, None
            output.close()
        self.pending.clear()
        self.pending_bytes = 0
