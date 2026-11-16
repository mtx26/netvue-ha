import json
import math
import socket
import time
from collections.abc import Callable

from .authentication import authenticate
from .connection import connect
from .discovery import discover
from .protocol import (
    ConnectionClosed, ProtocolError, RecoverableVideoError, command_packet, parse_units,
    read_packet,
)
from .rtsp import H264Publisher


class StreamInterrupted(ProtocolError):
    pass


def receive_stream(sock: socket.socket, publisher: H264Publisher, duration: float,
                   on_video: Callable[[H264Publisher], None] | None = None,
                   video_timeout: float = 3) -> None:
    if duration <= 0 or not math.isfinite(video_timeout) or video_timeout <= 0:
        raise ValueError("Duration and video timeout must be positive")
    sock.settimeout(2)
    print("live: sending one request", flush=True)
    now = int(time.time())
    sock.sendall(command_packet([{"Q": 3, "V": 2, "P": [
        1, [[1, 0]], [now, now + 31536000]
    ]}]))
    deadline = time.monotonic() + duration
    last_video = time.monotonic()
    channel_ok = False
    while time.monotonic() < deadline:
        try:
            kind, body = read_packet(sock, min(deadline, last_video + video_timeout))
        except (socket.timeout, TimeoutError):
            if time.monotonic() >= deadline:
                break
            raise StreamInterrupted("Video stream interrupted or stalled")
        if kind == 200:
            for unit in parse_units(body):
                if unit.get("A") == 3:
                    if type(unit.get("C")) is not int:
                        raise ProtocolError("LIVE selection lacks an explicit result")
                    if unit["C"] != 0:
                        raise ProtocolError(f"LIVE selection rejected, result_code={unit['C']}")
                    if channel_ok and publisher.published_frames > 0:
                        publisher.note_repeated_live_confirmation()
                    channel_ok = True
                    print("live: result_code=0", flush=True)
        elif kind == 201:
            if not channel_ok:
                raise ProtocolError("Media arrived before LIVE confirmation")
            if len(body) < 6:
                raise ProtocolError("Truncated media frame")
            frame_type = body[0] >> 4
            if frame_type == 1:
                before = publisher.published_frames
                publisher.push(body[5:], int.from_bytes(body[1:5], "little"))
                if before == 0 and publisher.published_frames > 0:
                    print("live: RTSP publisher ready", flush=True)
                last_video = time.monotonic()
                if on_video is not None:
                    on_video(publisher)
            elif frame_type != 2:
                raise ProtocolError("Unexpected media frame type")
        else:
            raise ProtocolError("Unexpected packet during LIVE")
    if not channel_ok:
        raise ProtocolError("LIVE confirmation missing")
    if publisher.video_packets == 0:
        raise ProtocolError("No video packets received")
    if publisher.sps is None:
        raise ProtocolError("H.264 SPS missing at end of LIVE")
    if publisher.pps is None:
        raise ProtocolError("H.264 PPS missing at end of LIVE")
    if not publisher.started:
        raise ProtocolError("H.264 IDR missing at end of LIVE")
    if publisher.config_changed:
        raise ProtocolError("H.264 parameter change unresolved at end of LIVE")
    if publisher.published_frames == 0:
        raise ProtocolError("No H.264 image decoded at end of LIVE")


def run_session(source: str, target: str, rtsp: str, duration: float,
                on_stage: Callable[[str], None] | None = None,
                on_video: Callable[[H264Publisher], None] | None = None,
                video_timeout: float = 3) -> str | None:
    if duration <= 0 or not math.isfinite(video_timeout) or video_timeout <= 0:
        raise ValueError("Invalid session limits")
    session_started = time.monotonic()
    deadline = session_started + duration
    publisher = None
    stage = "discovery"
    stage_started = session_started
    timings: dict[str, float] = {}
    try:
        if on_stage is not None:
            on_stage(stage)
        print("discovery: sending one request", flush=True)
        camera = discover(source, target)
        timings["discovery_seconds"] = round(time.monotonic() - stage_started, 3)
        if camera.port != 5555:
            raise ProtocolError("Discovered port differs from the authorized port")

        stage = "connect"
        if on_stage is not None:
            on_stage(stage)
        stage_started = time.monotonic()
        print("tcp: connecting once", flush=True)
        with connect(camera) as sock:
            timings["tcp_seconds"] = round(time.monotonic() - stage_started, 3)
            stage = "authentication"
            if on_stage is not None:
                on_stage(stage)
            stage_started = time.monotonic()
            print("authentication: sending one request", flush=True)
            result = authenticate(sock, camera)
            timings["authentication_seconds"] = round(time.monotonic() - stage_started, 3)
            print(f"authentication: result_code={result}", flush=True)
            if result != 0:
                return "blocked"

            stage = "live"
            if on_stage is not None:
                on_stage(stage)
            stage_started = time.monotonic()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Session deadline expired before LIVE")
            publisher = H264Publisher(rtsp)
            receive_stream(sock, publisher, remaining, on_video, video_timeout)
            print(f"live: published_frames={publisher.published_frames} "
                  f"key_frames={publisher.key_frames} "
                  f"parameter_updates={publisher.parameter_updates}", flush=True)
        return None
    except (StreamInterrupted, ConnectionClosed, TimeoutError, OSError) as exc:
        print(f"{stage}: interrupted={type(exc).__name__}", flush=True)
        return stage if stage != "authentication" else "blocked"
    except RecoverableVideoError as exc:
        print(f"{stage}: video_error={exc}", flush=True)
        return "video" if stage == "live" else "blocked"
    except ProtocolError as exc:
        print(f"{stage}: error={exc}", flush=True)
        return "blocked"
    except ValueError:
        print(f"{stage}: error=ValueError", flush=True)
        return "blocked"
    finally:
        timing_key = f"{stage}_seconds"
        if timing_key not in timings:
            timings[timing_key] = round(time.monotonic() - stage_started, 3)
        if publisher is not None:
            if isinstance(publisher.first_video_time, (int, float)):
                timings["first_video_delay_seconds"] = round(
                    publisher.first_video_time - session_started, 3)
            print("live_metrics: " + json.dumps(publisher.safe_metrics()), flush=True)
            publisher.close()
        print("session_timings: " + json.dumps(timings), flush=True)


if __name__ == "__main__":
    raise SystemExit("netvue_bridge.stream is internal; use netvue_bridge.continuous")
