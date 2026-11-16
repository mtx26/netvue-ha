import argparse
import json
import socket
import time

from .authentication import authenticate
from .connection import connect
from .discovery import discover
from .protocol import ConnectionClosed, ProtocolError, parse_units, read_packet
from .rtsp import H264Publisher, validate_local_rtsp_url
from .video import live_packet


class StreamInterrupted(ProtocolError):
    pass


def receive_stream(sock: socket.socket, publisher: H264Publisher, duration: float) -> None:
    if duration <= 0:
        raise ValueError("Duration must be positive")
    sock.settimeout(2)
    sock.sendall(live_packet(int(time.time())))
    deadline = time.monotonic() + duration
    last_video = time.monotonic()
    channel_ok = False
    while time.monotonic() < deadline:
        try:
            kind, body = read_packet(sock, min(deadline, last_video + 3))
        except (socket.timeout, TimeoutError):
            if time.monotonic() >= deadline:
                break
            raise StreamInterrupted("Video stream interrupted or stalled")
        if kind == 200:
            for unit in parse_units(body):
                if unit.get("A") == 3:
                    if type(unit.get("C")) is not int or unit["C"] != 0:
                        raise ProtocolError("LIVE selection was rejected or lacks a result")
                    channel_ok = True
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


def run_sessions(source: str, target: str, rtsp: str, duration: float,
                 max_attempts: int = 1, retry_delay: float = 1.0) -> int:
    if max_attempts < 1 or duration <= 0 or retry_delay < 0:
        raise ValueError("Invalid session limits")
    deadline = time.monotonic() + duration
    for attempt in range(1, max_attempts + 1):
        publisher = None
        stage = "discovery"
        try:
            camera = discover(source, target)
            if camera.port != 5555:
                raise ProtocolError("Discovered port differs from the authorized port")
            stage = "connect"
            with connect(camera) as sock:
                stage = "authentication"
                print("authentication: sending one request", flush=True)
                result = authenticate(sock, camera)
                print(f"authentication: result_code={result}", flush=True)
                if result != 0:
                    return 1
                stage = "live"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Session deadline expired before LIVE")
                publisher = H264Publisher(rtsp)
                receive_stream(sock, publisher, remaining)
                print(f"live: published_frames={publisher.published_frames} "
                      f"key_frames={publisher.key_frames} "
                      f"parameter_updates={publisher.parameter_updates}", flush=True)
            return 0
        except (StreamInterrupted, ConnectionClosed, TimeoutError, OSError) as exc:
            print(f"{stage}: interrupted={type(exc).__name__}", flush=True)
            if (stage == "authentication" or attempt == max_attempts
                    or time.monotonic() + retry_delay >= deadline):
                return 1
        except ProtocolError as exc:
            print(f"{stage}: error={exc}", flush=True)
            return 1
        except ValueError:
            print(f"{stage}: error=ValueError", flush=True)
            return 1
        finally:
            if publisher is not None:
                print("live_metrics: " + json.dumps(publisher.safe_metrics()), flush=True)
                publisher.close()
        print("session: retrying after interruption", flush=True)
        time.sleep(retry_delay)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="One bounded authorized local RTSP test")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--rtsp", default="rtsp://127.0.0.1:8554/netvue2")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--duration", type=float, default=10)
    modes.add_argument("--continuous", action="store_true")
    parser.add_argument("--max-attempts", type=int, choices=range(1, 4), default=1)
    args = parser.parse_args()
    if not 0 < args.duration <= 3600:
        parser.error("duration must be between 0 and 3600 seconds")
    validate_local_rtsp_url(args.rtsp)
    duration = float("inf") if args.continuous else args.duration
    try:
        return run_sessions(args.source, args.target, args.rtsp, duration,
                            max_attempts=args.max_attempts)
    except KeyboardInterrupt:
        print("session: interrupted by user", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
