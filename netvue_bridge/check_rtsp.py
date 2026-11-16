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


def partial_probe_array(text: str, name: str) -> list[dict]:
    marker = f'"{name}"'
    position = text.find(marker)
    if position < 0:
        return []
    position = text.find("[", position + len(marker))
    if position < 0:
        return []
    decoder = json.JSONDecoder()
    result = []
    position += 1
    while True:
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
        if position >= len(text) or text[position] == "]":
            break
        try:
            item, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            break
        if not isinstance(item, dict):
            break
        result.append(item)
    return result


def finish_readers(processes: dict[str, subprocess.Popen],
                   workers: dict[str, threading.Thread]) -> None:
    for process in processes.values():
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 3
    for process in processes.values():
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
    deadline = time.monotonic() + 3
    for process in processes.values():
        process.wait(timeout=max(0, deadline - time.monotonic()))
    deadline = time.monotonic() + 3
    for worker in workers.values():
        worker.join(timeout=max(0, deadline - time.monotonic()))


def enforce_bridge_deadline(process: subprocess.Popen, deadline: float) -> bool:
    if process.poll() is None and time.monotonic() >= deadline:
        try:
            process.kill()
        except ProcessLookupError:
            return False
        return True
    return False


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
            try:
                text = stdout.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            streams = partial_probe_array(text, "streams")
            packets = partial_probe_array(text, "packets")
        else:
            streams = data.get("streams", [])
            packets = data.get("packets", [])
        if streams:
            info.update({key: streams[0].get(key) for key in ("codec_name", "width", "height")})
        info["packets"] = len(packets)
        info["keyframes"] = sum("K" in packet.get("flags", "") for packet in packets)
        times = []
        invalid_times = 0
        for packet in packets:
            if "pts_time" in packet:
                try:
                    times.append(float(packet["pts_time"]))
                except (TypeError, ValueError):
                    invalid_times += 1
        if invalid_times:
            info["invalid_timestamp_values"] = invalid_times
        if times:
            info["first_pts_seconds"] = times[0]
            info["last_pts_seconds"] = times[-1]
        if len(times) >= 2:
            gaps = [b - a for a, b in zip(times, times[1:])]
            span = times[-1] - times[0]
            info["timestamp_span_seconds"] = round(span, 3)
            info["timestamps_strictly_increasing"] = all(gap > 0 for gap in gaps)
            info["max_timestamp_gap_seconds"] = round(max(gaps), 3)
            if span > 0:
                info["fps_from_timestamps"] = round((len(times) - 1) / span, 2)
        info["connection_verified"] = bool(packets)
        result[name] = info
    for name in ("ffmpeg", "home_assistant_ffmpeg"):
        if name not in outputs:
            continue
        stdout, stderr = outputs[name]
        counts, fps, durations = [], [], []
        invalid_values = 0
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            key, separator, value = line.partition("=")
            if not separator or key not in ("frame", "fps", "out_time_us"):
                continue
            try:
                if key == "frame":
                    counts.append(int(value))
                elif key == "fps":
                    fps.append(float(value))
                else:
                    durations.append(int(value))
            except ValueError:
                invalid_values += 1
        result[name] = {"decoded_frames": max(counts, default=0),
                        "reported_fps": fps[-1] if fps else None,
                        "decoded_duration_seconds": round(max(durations) / 1000000, 3)
                        if durations else None,
                        "invalid_progress_values": invalid_values,
                        "connection_verified": bool(counts and max(counts) > 0),
                        "error_lines": len(stderr.splitlines())}
    return result


def validate_local_readers(probe_out: bytes, probe_error: bytes, player_out: bytes,
                           player_error: bytes, probe_duration: float,
                           startup_seconds: float, reader_delay_seconds: float) -> dict[str, object]:
    info = json.loads(probe_out)
    streams, packets = info.get("streams", []), info.get("packets", [])
    if not streams or streams[0].get("codec_name") != "h264":
        raise RuntimeError("FFprobe did not identify an H.264 stream")
    times = [float(p["pts_time"]) for p in packets if "pts_time" in p]
    if len(times) < 2 or not all(b > a for a, b in zip(times, times[1:])):
        raise RuntimeError("RTSP packet timestamps are insufficient or non-monotonic")
    span = times[-1] - times[0]
    max_gap = max(b - a for a, b in zip(times, times[1:]))
    if span < probe_duration * 0.8 or max_gap > 1:
        raise RuntimeError("RTSP packet continuity did not meet the test threshold")
    keys = sum("K" in p.get("flags", "") for p in packets)
    frame_counts = [int(line.split("=", 1)[1]) for line in player_out.decode().splitlines()
                    if line.startswith("frame=")]
    if not keys or not frame_counts or max(frame_counts) < 2:
        raise RuntimeError("RTSP image keyframe or decoded image not observed")
    return {
        "codec": streams[0]["codec_name"], "width": streams[0].get("width"),
        "height": streams[0].get("height"), "packets": len(packets),
        "keyframes": keys, "timestamped_packets": len(times),
        "decoded_frames": max(frame_counts),
        "first_pts_seconds": times[0],
        "last_pts_seconds": times[-1],
        "timestamp_span_seconds": round(span, 3),
        "max_timestamp_gap_seconds": round(max_gap, 3),
        "fps_from_timestamps": round((len(times) - 1) / span, 2),
        "startup_seconds": round(startup_seconds, 3),
        "reader_delay_seconds": reader_delay_seconds,
        "ffprobe_error_lines": len(probe_error.splitlines()),
        "ffmpeg_error_lines": len(player_error.splitlines()),
    }


def build_reader_commands(args: argparse.Namespace, container: str | None) -> list[tuple[str, list[str]]]:
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
        reader_timeout = str(int(args.duration + 8))
        readers.append(("home_assistant_ffprobe", [
            "docker", "exec", container, "timeout", "-k", "2", reader_timeout,
            "ffprobe", *probe_args,
        ]))
        readers.append(("home_assistant_ffmpeg", [
            "docker", "exec", container, "timeout", "-k", "2", reader_timeout,
            "ffmpeg", "-v", "error",
            "-rtsp_transport", "tcp", "-i", URL, "-t", str(args.probe_duration),
            "-f", "null", "-", "-progress", "pipe:1", "-nostats",
        ]))
    return readers


def validate_frame_probe(stdout: bytes, stderr: bytes, returncode: int | None) -> list[tuple[int, int]]:
    if returncode != 0:
        raise RuntimeError(f"Frame probe failed; error lines={len(stderr.splitlines())}")
    frames = json.loads(stdout).get("frames", [])
    sizes = sorted({(frame["width"], frame["height"]) for frame in frames
                    if "width" in frame and "height" in frame})
    if not sizes:
        raise RuntimeError("Frame probe did not decode image dimensions")
    return sizes


def validate_home_assistant_probe(stdout: bytes, stderr: bytes, returncode: int | None,
                                  local_summary: dict[str, object]) -> dict[str, object]:
    if returncode != 0:
        raise RuntimeError(f"Home Assistant FFprobe failed; error lines={len(stderr.splitlines())}")
    info = json.loads(stdout)
    streams, packets = info.get("streams", []), info.get("packets", [])
    if (not streams or streams[0].get("codec_name") != "h264"
            or streams[0].get("width") != local_summary["width"]
            or streams[0].get("height") != local_summary["height"]
            or len(packets) < 2):
        raise RuntimeError("Home Assistant FFprobe could not verify the RTSP stream")
    return {
        "codec": streams[0]["codec_name"], "width": streams[0]["width"],
        "height": streams[0]["height"], "packets": len(packets),
        "ffprobe_error_lines": len(stderr.splitlines()),
    }


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


def parse_arguments() -> tuple[argparse.Namespace, str | None]:
    parser = argparse.ArgumentParser(description="One bounded local RTSP camera check")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--mediamtx", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--probe-duration", type=float, default=3)
    parser.add_argument("--reader-delay", type=float, default=0)
    parser.add_argument("--bridge-timeout", type=float)
    parser.add_argument("--home-assistant-check", action="store_true")
    parser.add_argument("--frame-probe", action="store_true")
    args = parser.parse_args()
    if not (0 < args.duration <= 30 and 0 < args.probe_duration
            and args.reader_delay >= 0
            and args.reader_delay + args.probe_duration <= args.duration - 3
            and (args.bridge_timeout is None
                 or args.duration < args.bridge_timeout <= 30)):
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
        result = subprocess.run(["docker", "exec", container, "timeout", "--help"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if result.returncode:
            parser.error("timeout is unavailable in the Home Assistant container")
    return args, container


def main() -> int:
    start = time.monotonic()
    args, container = parse_arguments()
    with socket.socket() as check:
        check.bind(("127.0.0.1", 8554))
    config = Path(__file__).with_name("mediamtx.local.yml")
    processes: dict[str, subprocess.Popen] = {}
    samples: dict[str, list[float | int]] = {}
    lines: queue.SimpleQueue[str] = queue.SimpleQueue()
    outputs: dict[str, tuple[bytes, bytes]] = {}
    drains: dict[str, threading.Thread] = {}
    bridge_lines: list[str] = []
    bridge_thread: threading.Thread | None = None
    bridge_errors: list[str] = []
    bridge_error_thread: threading.Thread | None = None
    bridge_started = None
    bridge_stopped_at = None
    ready_at = None

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
        if time.monotonic() - start > 35:
            raise RuntimeError("Preflight took too long to start a bounded session")
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

        bridge_thread = threading.Thread(target=read_bridge)
        bridge_thread.start()

        def read_bridge_errors() -> None:
            for _ in bridge.stderr:
                bridge_errors.append("")

        bridge_error_thread = threading.Thread(target=read_bridge_errors)
        bridge_error_thread.start()
        ready = False
        readers_started = False
        while (time.monotonic() - bridge_started < args.duration + 12
               and time.monotonic() - start < 65):
            collect()
            if (args.bridge_timeout is not None
                    and enforce_bridge_deadline(bridge, bridge_started + args.bridge_timeout)):
                raise RuntimeError("Bridge exceeded the publication watchdog deadline")
            while not lines.empty():
                line = lines.get_nowait()
                bridge_lines.append(line)
                if line == "live: RTSP publisher ready" and not ready:
                    ready = True
                    ready_at = time.monotonic()
            if ready_at is not None and not readers_started and time.monotonic() >= ready_at + args.reader_delay:
                readers_started = True
                for name, cmd in build_reader_commands(args, container):
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
        bridge_thread.join(timeout=1)
        while not lines.empty():
            bridge_lines.append(lines.get_nowait())
        drain_deadline = time.monotonic() + 3
        for worker in drains.values():
            worker.join(timeout=max(0, drain_deadline - time.monotonic()))
        reader_summary = partial_diagnostics(outputs)
        for name in drains:
            reader_summary.setdefault(name, {})["returncode"] = processes[name].poll()
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
        summary = validate_local_readers(
            probe_out, probe_error, player_out, player_error, args.probe_duration,
            ready_at - bridge_started, args.reader_delay,
        )
        print("rtsp:", json.dumps(summary))
        if args.frame_probe:
            frame_probe = processes["frame_probe"]
            frames_out, frames_error = outputs["frame_probe"]
            sizes = validate_frame_probe(frames_out, frames_error, frame_probe.returncode)
            print("decoded_dimensions:", json.dumps(sizes))
        if container is not None:
            ha = processes["home_assistant_ffprobe"]
            ha_out, ha_error = outputs["home_assistant_ffprobe"]
            print("home_assistant_rtsp:", json.dumps(validate_home_assistant_probe(
                ha_out, ha_error, ha.returncode, summary,
            )))
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
        for name in ("bridge", "mediamtx"):
            process = processes.get(name)
            if process is None:
                continue
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        if bridge_thread is not None:
            bridge_thread.join(timeout=3)
        if bridge_error_thread is not None:
            bridge_error_thread.join(timeout=3)
        while not lines.empty():
            bridge_lines.append(lines.get_nowait())
        if "bridge" in processes:
            print("bridge:", json.dumps(bridge_lines), flush=True)
            print("bridge_stderr_lines:", len(bridge_errors), flush=True)
        if bridge_started is not None and bridge_stopped_at is not None:
            print("bridge_process_seconds:", round(bridge_stopped_at - bridge_started, 3))
        if bridge_started is not None and ready_at is not None:
            print("publisher_ready_seconds:", round(ready_at - bridge_started, 3))
        readers = {name: processes[name] for name in drains}
        if any(process.poll() is None for process in readers.values()):
            time.sleep(1)
        finish_readers(readers, drains)
        reader_summary = partial_diagnostics(outputs)
        for name, process in readers.items():
            reader_summary.setdefault(name, {})["returncode"] = process.returncode
            if name not in outputs:
                reader_summary[name]["output_unavailable"] = True
        print("readers:", json.dumps(reader_summary), flush=True)
        for process in processes.values():
            for pipe in (process.stdout, process.stderr):
                if pipe is not None and not pipe.closed:
                    pipe.close()
        elapsed = time.monotonic() - start
        metrics = {}
        for name, (began, cpu, rss) in samples.items():
            wall = max(0.001, elapsed - (began - start))
            metrics[name] = {"sampled_cpu_seconds": round(cpu, 2),
                             "sampled_cpu_percent": round(cpu / wall * 100, 1),
                             "peak_sampled_rss_kib": rss}
        print("resources:", json.dumps(metrics), flush=True)
        print("check_total_seconds:", round(elapsed, 3), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
