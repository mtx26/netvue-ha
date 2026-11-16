import argparse
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


URL = "rtsp://127.0.0.1:8554/netvue2"


def partial_diagnostics(outputs: dict[str, tuple[bytes, bytes]]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for name in ("ffprobe", "home_assistant_ffprobe"):
        if name not in outputs:
            continue
        stdout, stderr = outputs[name]
        info: dict[str, object] = {"error_lines": len(stderr.splitlines())}
        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, UnicodeDecodeError):
            info["incomplete_json"] = True
        else:
            streams = data.get("streams", [])
            packets = data.get("packets", [])
            if streams:
                info.update({key: streams[0].get(key) for key in ("codec_name", "width", "height")})
            info["packets"] = len(packets)
            info["keyframes"] = sum("K" in packet.get("flags", "") for packet in packets)
            times = [float(p["pts_time"]) for p in packets if "pts_time" in p]
            if len(times) >= 2 and times[-1] > times[0]:
                info["fps_from_timestamps"] = round((len(times) - 1) / (times[-1] - times[0]), 2)
            info["connection_verified"] = bool(packets)
        result[name] = info
    for name in ("ffmpeg", "home_assistant_ffmpeg"):
        if name not in outputs:
            continue
        stdout, stderr = outputs[name]
        counts = [int(line.split("=", 1)[1]) for line in stdout.decode().splitlines()
                  if line.startswith("frame=")]
        fps = [float(line.split("=", 1)[1]) for line in stdout.decode().splitlines()
               if line.startswith("fps=")]
        result[name] = {"decoded_frames": max(counts, default=0),
                        "reported_fps": fps[-1] if fps else None,
                        "connection_verified": bool(counts and max(counts) > 0),
                        "error_lines": len(stderr.splitlines())}
    return result


def sample(pid: int) -> tuple[float, int] | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat[stat.rfind(")") + 2:].split()
        cpu = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
        status = Path(f"/proc/{pid}/status").read_text().splitlines()
        rss = next(int(line.split()[1]) for line in status if line.startswith("VmRSS:"))
        return cpu, rss
    except (FileNotFoundError, ProcessLookupError, StopIteration):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="One bounded local RTSP camera check")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--mediamtx", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--probe-duration", type=float, default=3)
    parser.add_argument("--reader-delay", type=float, default=0)
    parser.add_argument("--home-assistant-check", action="store_true")
    parser.add_argument("--frame-probe", action="store_true")
    args = parser.parse_args()
    if not (0 < args.duration <= 30 and 0 < args.probe_duration
            and args.reader_delay >= 0
            and args.reader_delay + args.probe_duration <= args.duration - 3):
        parser.error("Invalid duration, probe duration or reader delay")
    for binary in (args.mediamtx, args.ffprobe, args.ffmpeg):
        if not os.access(binary, os.X_OK):
            parser.error("All three portable executables must be available")
    container = None
    if args.home_assistant_check:
        result = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                                capture_output=True, text=True, timeout=10, check=True)
        candidates = [name for name in result.stdout.splitlines()
                      if "homeassistant" in name.lower() or "home-assistant" in name.lower()]
        if len(candidates) != 1:
            parser.error("Expected exactly one running Home Assistant container")
        container = candidates[0]
        result = subprocess.run(["docker", "inspect", "--format", "{{.HostConfig.NetworkMode}}",
                                 container], capture_output=True, text=True, timeout=10, check=True)
        if result.stdout.strip() != "host":
            parser.error("Home Assistant container is not using host networking")
        for binary in ("ffprobe", "ffmpeg"):
            result = subprocess.run(["docker", "exec", container, binary, "-version"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if result.returncode:
                parser.error(f"{binary} is unavailable in the Home Assistant container")
    with socket.socket() as check:
        check.bind(("127.0.0.1", 8554))
    config = Path(__file__).with_name("mediamtx.local.yml")
    processes: dict[str, subprocess.Popen] = {}
    samples: dict[str, list[float | int]] = {}
    lines: queue.SimpleQueue[str] = queue.SimpleQueue()
    outputs: dict[str, tuple[bytes, bytes]] = {}
    drains: dict[str, threading.Thread] = {}
    start = time.monotonic()

    def launch(name: str, cmd: list[str], **kwargs) -> subprocess.Popen:
        process = subprocess.Popen(cmd, **kwargs)
        processes[name] = process
        samples[name] = [time.monotonic(), 0.0, 0]
        return process

    def collect() -> None:
        for name, process in processes.items():
            reading = sample(process.pid)
            if reading is not None:
                cpu, rss = reading
                samples[name][1] = max(samples[name][1], cpu)
                samples[name][2] = max(samples[name][2], rss)

    def drain(name: str, process: subprocess.Popen) -> None:
        outputs[name] = process.communicate()

    try:
        server = launch("mediamtx", [args.mediamtx, str(config)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while True:
            if server.poll() is not None:
                raise RuntimeError("MediaMTX failed to start")
            try:
                with socket.create_connection(("127.0.0.1", 8554), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("MediaMTX did not become ready")
                time.sleep(0.05)
        if server.poll() is not None:
            raise RuntimeError("MediaMTX exited during startup")
        bridge = launch(
            "bridge", [sys.executable, "-B", "-m", "netvue_bridge.stream",
                       "--source", args.source, "--target", args.target,
                       "--duration", str(args.duration), "--max-attempts", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        bridge_started = time.monotonic()

        def read_bridge() -> None:
            for line in bridge.stdout:
                lines.put(line.strip())

        thread = threading.Thread(target=read_bridge)
        thread.start()
        ready = False
        ready_at = None
        readers_started = False
        bridge_lines = []
        bridge_stopped_at = None
        while time.monotonic() - start < args.duration + 12:
            collect()
            while not lines.empty():
                line = lines.get_nowait()
                bridge_lines.append(line)
                if line == "live: RTSP publisher ready" and not ready:
                    ready = True
                    ready_at = time.monotonic()
            if ready_at is not None and not readers_started and time.monotonic() >= ready_at + args.reader_delay:
                readers_started = True
                probe_args = [
                    "-v", "error", "-rtsp_transport", "tcp",
                    "-select_streams", "v:0", "-show_entries",
                    "stream=codec_name,width,height:packet=pts_time,flags",
                    "-show_streams", "-show_packets", "-read_intervals",
                    f"%+{args.probe_duration}", "-of", "json", URL,
                ]
                readers = [
                    ("ffprobe", [args.ffprobe, *probe_args]),
                    ("ffmpeg", [
                        args.ffmpeg, "-v", "error", "-rtsp_transport", "tcp",
                        "-i", URL, "-t", str(args.probe_duration), "-f", "null", "-",
                        "-progress", "pipe:1", "-nostats",
                    ]),
                ]
                if args.frame_probe:
                    readers.append(("frame_probe", [
                        args.ffprobe, "-v", "error", "-rtsp_transport", "tcp",
                        "-select_streams", "v:0", "-show_entries", "frame=width,height",
                        "-show_frames", "-read_intervals", f"%+{args.probe_duration}",
                        "-of", "json", URL,
                    ]))
                if container is not None:
                    readers.append(("home_assistant_ffprobe", [
                        "docker", "exec", container, "ffprobe", *probe_args,
                    ]))
                    readers.append(("home_assistant_ffmpeg", [
                        "docker", "exec", container, "ffmpeg", "-v", "error",
                        "-rtsp_transport", "tcp", "-i", URL, "-t", str(args.probe_duration),
                        "-f", "null", "-", "-progress", "pipe:1", "-nostats",
                    ]))
                for name, cmd in readers:
                    process = launch(name, cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    worker = threading.Thread(target=drain, args=(name, process))
                    drains[name] = worker
                    worker.start()
            if bridge.poll() is not None and bridge_stopped_at is None:
                bridge_stopped_at = time.monotonic()
            if bridge_stopped_at is not None and all(
                    process.poll() is not None for name, process in processes.items()
                    if name in ("ffprobe", "ffmpeg", "home_assistant_ffprobe",
                                "home_assistant_ffmpeg", "frame_probe")):
                break
            time.sleep(0.1)
        thread.join(timeout=1)
        while not lines.empty():
            bridge_lines.append(lines.get_nowait())
        print("bridge:", json.dumps(bridge_lines))
        if bridge_stopped_at is not None:
            print("bridge_process_seconds:", round(bridge_stopped_at - bridge_started, 3))
        for worker in drains.values():
            worker.join(timeout=3)
        reader_summary = partial_diagnostics(outputs)
        for name in drains:
            reader_summary.setdefault(name, {})["returncode"] = processes[name].poll()
        print("readers:", json.dumps(reader_summary), flush=True)
        if bridge.poll() is None or bridge.returncode != 0 or not ready:
            raise RuntimeError("Bridge did not complete successfully")
        if not readers_started:
            raise RuntimeError("RTSP readers did not start")
        probe, player = processes["ffprobe"], processes["ffmpeg"]
        if any(processes[name].poll() is None for name in drains):
            raise RuntimeError("Local readers did not finish in time")
        if len(outputs) != len(drains):
            raise RuntimeError("Unable to collect local reader output")
        probe_out, probe_error = outputs["ffprobe"]
        player_out, player_error = outputs["ffmpeg"]
        if probe.returncode != 0 or player.returncode != 0:
            raise RuntimeError(f"An RTSP reader failed; ffprobe error lines={len(probe_error.splitlines())}, "
                               f"ffmpeg error lines={len(player_error.splitlines())}")
        info = json.loads(probe_out)
        streams, packets = info.get("streams", []), info.get("packets", [])
        if not streams or streams[0].get("codec_name") != "h264":
            raise RuntimeError("FFprobe did not identify an H.264 stream")
        times = [float(p["pts_time"]) for p in packets if "pts_time" in p]
        if len(times) < 2 or not all(b > a for a, b in zip(times, times[1:])):
            raise RuntimeError("RTSP packet timestamps are insufficient or non-monotonic")
        span = times[-1] - times[0]
        max_gap = max(b - a for a, b in zip(times, times[1:]))
        if span < args.probe_duration * 0.8 or max_gap > 1:
            raise RuntimeError("RTSP packet continuity did not meet the test threshold")
        keys = sum("K" in p.get("flags", "") for p in packets)
        frame_counts = [int(line.split("=", 1)[1]) for line in player_out.decode().splitlines()
                        if line.startswith("frame=")]
        if not keys or not frame_counts or max(frame_counts) < 2:
            raise RuntimeError("RTSP image keyframe or decoded image not observed")
        summary = {
            "codec": streams[0]["codec_name"], "width": streams[0].get("width"),
            "height": streams[0].get("height"), "packets": len(packets),
            "keyframes": keys, "timestamped_packets": len(times),
            "decoded_frames": max(frame_counts),
            "timestamp_span_seconds": round(span, 3),
            "max_timestamp_gap_seconds": round(max_gap, 3),
            "fps_from_timestamps": round((len(times) - 1) / span, 2),
            "startup_seconds": round(ready_at - bridge_started, 3),
            "reader_delay_seconds": args.reader_delay,
            "ffprobe_error_lines": len(probe_error.splitlines()),
            "ffmpeg_error_lines": len(player_error.splitlines()),
        }
        print("rtsp:", json.dumps(summary))
        if args.frame_probe:
            frame_probe = processes["frame_probe"]
            frames_out, frames_error = outputs["frame_probe"]
            if frame_probe.returncode != 0:
                raise RuntimeError(f"Frame probe failed; error lines={len(frames_error.splitlines())}")
            frames = json.loads(frames_out).get("frames", [])
            sizes = sorted({(frame["width"], frame["height"]) for frame in frames
                            if "width" in frame and "height" in frame})
            if not sizes:
                raise RuntimeError("Frame probe did not decode image dimensions")
            print("decoded_dimensions:", json.dumps(sizes))
        if container is not None:
            ha = processes["home_assistant_ffprobe"]
            ha_out, ha_error = outputs["home_assistant_ffprobe"]
            if ha.returncode != 0:
                raise RuntimeError(f"Home Assistant FFprobe failed; error lines={len(ha_error.splitlines())}")
            ha_info = json.loads(ha_out)
            ha_streams, ha_packets = ha_info.get("streams", []), ha_info.get("packets", [])
            if (not ha_streams or ha_streams[0].get("codec_name") != "h264"
                    or ha_streams[0].get("width") != summary["width"]
                    or ha_streams[0].get("height") != summary["height"]
                    or len(ha_packets) < 2):
                raise RuntimeError("Home Assistant FFprobe could not verify the RTSP stream")
            print("home_assistant_rtsp:", json.dumps({
                "codec": ha_streams[0]["codec_name"], "width": ha_streams[0]["width"],
                "height": ha_streams[0]["height"], "packets": len(ha_packets),
                "ffprobe_error_lines": len(ha_error.splitlines()),
            }))
            ha_player = processes["home_assistant_ffmpeg"]
            ha_player_info = reader_summary.get("home_assistant_ffmpeg", {})
            ha_frames = ha_player_info.get("decoded_frames")
            if ha_player.returncode != 0 or not isinstance(ha_frames, int) or ha_frames < 2:
                raise RuntimeError("Home Assistant FFmpeg did not decode multiple frames")
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"check: failed ({type(exc).__name__}): {exc}", flush=True)
        return 1
    finally:
        collect()
        elapsed = time.monotonic() - start
        for process in reversed(list(processes.values())):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        for worker in drains.values():
            worker.join(timeout=3)
        for process in processes.values():
            for pipe in (process.stdout, process.stderr):
                if pipe is not None and not pipe.closed:
                    pipe.close()
        metrics = {}
        for name, (began, cpu, rss) in samples.items():
            wall = max(0.001, elapsed - (began - start))
            metrics[name] = {"sampled_cpu_seconds": round(cpu, 2),
                             "sampled_cpu_percent": round(cpu / wall * 100, 1),
                             "peak_sampled_rss_kib": rss}
        print("resources:", json.dumps(metrics), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
