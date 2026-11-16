import io
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from .protocol import ProtocolError, command_packet, parse_units, read_packet


MAX_CAPTURE = 8 * 1024 * 1024


def live_packet(now: int) -> bytes:
    return command_packet([{"Q": 3, "V": 2, "P": [
        1, [[1, 0]], [now, now + 31536000]
    ]}])


@dataclass
class VideoSummary:
    video_frames: int = 0
    key_frames: int = 0
    audio_frames: int = 0
    first_pts: int | None = None
    last_pts: int | None = None
    channel_result: int | None = None
    encoded_bytes: int = 0
    capture_truncated: bool = False
    _data: bytearray = field(default_factory=bytearray, repr=False)

    def add_frame(self, body: bytes) -> None:
        if len(body) < 5:
            raise ProtocolError("Truncated media frame")
        frame_type = body[0] >> 4
        pts = int.from_bytes(body[1:5], "little")
        if frame_type == 1:
            self.video_frames += 1
            self.key_frames += bool(body[0] & 1)
            if self.first_pts is None:
                self.first_pts = pts
            self.last_pts = pts
            data = body[5:]
            self.encoded_bytes += len(data)
            if len(self._data) + len(data) <= MAX_CAPTURE:
                self._data.extend(data)
            else:
                self.capture_truncated = True
        elif frame_type == 2:
            self.audio_frames += 1
        else:
            raise ProtocolError("Unexpected media frame type")

    def test_h264_decode(self) -> dict[str, Any]:
        if not self._data:
            return {"status": "no_video"}
        try:
            import av
        except ModuleNotFoundError as exc:
            if exc.name != "av":
                raise
        else:
            try:
                with av.open(io.BytesIO(self._data), mode="r", format="h264") as container:
                    frame = next(container.decode(video=0), None)
                    if frame is not None:
                        return {"status": "decoded_h264_image_in_memory",
                                "width": frame.width, "height": frame.height}
            except av.error.FFmpegError:
                return {"status": "h264_decode_failed"}
            return {"status": "no_decoded_image"}
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            return {"status": "decoder_unavailable"}
        try:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "h264",
                 "-i", "pipe:0", "-frames:v", "1", "-f", "image2pipe", "-vcodec",
                 "png", "pipe:1"],
                input=bytes(self._data), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=8, check=False,
            )
        except subprocess.TimeoutExpired:
            return {"status": "decoder_timeout"}
        if result.returncode == 0 and result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            return {"status": "decoded_h264_image_in_memory"}
        return {"status": "h264_decode_failed"}

    def safe_result(self) -> dict[str, Any]:
        return {
            "video_frames": self.video_frames,
            "key_frames": self.key_frames,
            "audio_frames": self.audio_frames,
            "first_pts": self.first_pts,
            "last_pts": self.last_pts,
            "channel_result": self.channel_result,
            "encoded_bytes": self.encoded_bytes,
            "capture_truncated": self.capture_truncated,
            "decode": self.test_h264_decode(),
        }


def receive_live(sock: socket.socket, duration: float = 10.0) -> VideoSummary:
    if not 0 < duration <= 10:
        raise ValueError("Live duration must be at most 10 seconds")
    sock.settimeout(2)
    sock.sendall(live_packet(int(time.time())))
    deadline = time.monotonic() + duration
    result = VideoSummary()
    while time.monotonic() < deadline:
        try:
            kind, body = read_packet(sock, deadline)
        except (socket.timeout, TimeoutError):
            break
        if kind == 201:
            result.add_frame(body)
        elif kind == 200:
            for unit in parse_units(body):
                if unit.get("A") == 3:
                    code = unit.get("C")
                    if type(code) is not int:
                        raise ProtocolError("Channel response has no explicit result code")
                    result.channel_result = code
                    if code != 0:
                        return result
        else:
            raise ProtocolError("Unexpected packet during live stream")
    return result
