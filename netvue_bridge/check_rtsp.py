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
    parser = argparse.ArgumentParser(description="One 10-second local RTSP camera check")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--mediamtx", required=True)
    parser.add_argument("--ffprobe", required=True)
    parser.add_argument("--ffmpeg", required=True)
    args = parser.parse_args()
    for binary in (args.mediamtx, args.ffprobe, args.ffmpeg):
        if not os.access(binary, os.X_OK):
            parser.error("All three portable executables must be available")
    with socket.socket() as check:
        check.bind(("127.0.0.1", 8554))
    config = Path(__file__).with_name("mediamtx.local.yml")
    processes: dict[str, subprocess.Popen] = {}
    samples: dict[str, list[float | int]] = {}
    lines: queue.SimpleQueue[str] = queue.SimpleQueue()
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
                       "--duration", "10", "--max-attempts", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )

        def read_bridge() -> None:
            for line in bridge.stdout:
                lines.put(line.strip())

        thread = threading.Thread(target=read_bridge)
        thread.start()
        ready = False
        bridge_lines = []
        while time.monotonic() - start < 17:
            collect()
            while not lines.empty():
                line = lines.get_nowait()
                bridge_lines.append(line)
                if line == "live: RTSP publisher ready" and not ready:
                    ready = True
                    launch("ffprobe", [
                        args.ffprobe, "-v", "error", "-rtsp_transport", "tcp",
                        "-select_streams", "v:0", "-show_entries",
                        "stream=codec_name,width,height:packet=pts_time,flags",
                        "-show_streams", "-show_packets", "-read_intervals", "%+3",
                        "-of", "json", URL,
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    launch("ffmpeg", [
                        args.ffmpeg, "-v", "error", "-rtsp_transport", "tcp",
                        "-i", URL, "-t", "3", "-f", "null", "-",
                        "-progress", "pipe:1", "-nostats",
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if bridge.poll() is not None and all(
                    process.poll() is not None for name, process in processes.items()
                    if name in ("ffprobe", "ffmpeg")):
                break
            time.sleep(0.1)
        thread.join(timeout=1)
        while not lines.empty():
            bridge_lines.append(lines.get_nowait())
        print("bridge:", json.dumps(bridge_lines))
        if bridge.poll() is None or bridge.returncode != 0 or not ready:
            raise RuntimeError("Bridge did not complete successfully")
        probe, player = processes["ffprobe"], processes["ffmpeg"]
        if probe.poll() is None or player.poll() is None:
            raise RuntimeError("Local readers did not finish in time")
        probe_out, _ = probe.communicate()
        player_out, _ = player.communicate()
        if probe.returncode != 0 or player.returncode != 0:
            raise RuntimeError("An RTSP reader failed")
        info = json.loads(probe_out)
        streams, packets = info.get("streams", []), info.get("packets", [])
        if not streams or streams[0].get("codec_name") != "h264":
            raise RuntimeError("FFprobe did not identify an H.264 stream")
        times = [float(p["pts_time"]) for p in packets if "pts_time" in p]
        if len(times) < 2 or not all(b > a for a, b in zip(times, times[1:])):
            raise RuntimeError("RTSP packet timestamps are insufficient or non-monotonic")
        keys = sum("K" in p.get("flags", "") for p in packets)
        frame_counts = [int(line.split("=", 1)[1]) for line in player_out.decode().splitlines()
                        if line.startswith("frame=")]
        if not keys or not frame_counts or max(frame_counts) == 0:
            raise RuntimeError("RTSP image keyframe or decoded image not observed")
        print("rtsp:", json.dumps({
            "codec": streams[0]["codec_name"], "width": streams[0].get("width"),
            "height": streams[0].get("height"), "packets": len(packets),
            "keyframes": keys, "timestamped_packets": len(times),
            "decoded_frames": max(frame_counts),
        }))
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
