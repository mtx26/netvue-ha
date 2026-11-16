"""Accelerated synthetic 2,000-second H.264 mux and reader validation."""

import json
import os
import resource
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import av

from netvue_bridge.rtsp import H264Publisher

FRAMES = 20000


def cgroup_cpu_usec():
    path = Path("/sys/fs/cgroup/cpu.stat")
    if not path.exists():
        return None
    return int(dict(line.split() for line in path.read_text().splitlines())["usage_usec"])


def encoder(width: int, cabac: int):
    codec = av.CodecContext.create("libx264", "w")
    codec.width, codec.height, codec.pix_fmt = width, 64, "yuv420p"
    codec.time_base = Fraction(1, 10)
    codec.options = {"preset": "ultrafast", "tune": "zerolatency",
                     "x264-params": f"keyint=10:min-keyint=10:scenecut=0:bframes=0:"
                                    f"slices=2:cabac={cabac}"}
    codec.open()
    return codec


def main():
    real_open = av.open
    started = time.perf_counter()
    cpu_start = time.process_time()
    cgroup_cpu_start = cgroup_cpu_usec()
    simulated = [0.0]
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "synthetic.ts")

        def open_synthetic(*args, **kwargs):
            return real_open(path, mode="w", format="mpegts")

        publisher = H264Publisher("rtsp://127.0.0.1:8554/synthetic")
        try:
            codec = encoder(64, 0)
            with patch("netvue_bridge.rtsp.time.monotonic", side_effect=lambda: simulated[0]), \
                    patch.object(av, "open", side_effect=open_synthetic):
                publisher.waiting_since = 0.0
                for index in range(FRAMES):
                    simulated[0] = index / 10
                    if index == 10000:
                        publisher.note_repeated_live_confirmation()
                        codec = encoder(128, 1)
                    frame = av.VideoFrame(64 if index < 10000 else 128, 64, "yuv420p")
                    frame.pts = index
                    for plane in frame.planes:
                        plane.update(bytes([index % 251]) * plane.buffer_size)
                    packets = codec.encode(frame)
                    if len(packets) != 1:
                        raise RuntimeError("Encoder did not produce exactly one access unit")
                    raw = index if index < 10000 else index - 10000
                    if index in (7650, 14500, 17000):
                        raw -= 1
                    elif index == 17001:
                        raw -= 2
                    publisher.push(bytes(packets[0]), raw * 100)
            metrics = publisher.safe_metrics()
        finally:
            publisher.close()
        if (metrics["published_frames"] != FRAMES or metrics["same_timestamps"] != 4
                or metrics["timestamp_corrections"] != 4 or metrics["timestamp_resets"] != 1
                or metrics["sps_changes"] < 1 or metrics["pps_changes"] < 1
                or metrics["resolution_changes"] != 1 or metrics["ignored_frames"] != 0
                or metrics["unresolved_timestamp_frames"] != 0):
            raise RuntimeError(f"Unexpected publisher metrics: {metrics}")
        probe = subprocess.run(
            [os.environ.get("NETVUE_FFPROBE", "/ffmpeg/ffprobe"), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time", "-show_packets", "-of", "json", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=120)
        if probe.stderr:
            raise RuntimeError("FFprobe reported decoding errors")
        times = [float(packet["pts_time"]) for packet in json.loads(probe.stdout)["packets"]]
        if len(times) != FRAMES or any(b <= a for a, b in zip(times, times[1:])):
            raise RuntimeError("FFprobe packet count or PTS order invalid")
        if abs(times[-1] - times[0] - (FRAMES - 1) / 10) > 0.2:
            raise RuntimeError("Unexpected timestamp drift")
        frames = subprocess.run(
            [os.environ.get("NETVUE_FFPROBE", "/ffmpeg/ffprobe"), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "frame=width,height", "-show_frames", "-of", "json", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=120)
        if frames.stderr:
            raise RuntimeError("FFprobe reported frame errors")
        dimensions = sorted({(frame["width"], frame["height"])
                             for frame in json.loads(frames.stdout)["frames"]})
        if dimensions != [(64, 64), (128, 64)]:
            raise RuntimeError(f"Unexpected decoded dimensions: {dimensions}")
        result = subprocess.run(
            [os.environ.get("NETVUE_FFMPEG", "/ffmpeg/ffmpeg"), "-v", "error",
             "-i", path, "-progress", "pipe:1", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=120)
        if result.stderr:
            raise RuntimeError("FFmpeg reported decoding errors")
        decoded = max(int(line.split("=", 1)[1]) for line in result.stdout.decode().splitlines()
                      if line.startswith("frame="))
        if decoded != FRAMES:
            raise RuntimeError(f"Unexpected FFmpeg decoded frame count: {decoded}")
        elapsed = time.perf_counter() - started
        cgroup_cpu_end = cgroup_cpu_usec()
        peak_path = Path("/sys/fs/cgroup/memory.peak")
        print(json.dumps({"frames": FRAMES, "pts_span_seconds": round(times[-1] - times[0], 3),
                          "max_gap_seconds": round(max(b - a for a, b in zip(times, times[1:])), 3),
                          "decoded_frames": decoded, "dimensions": dimensions, "metrics": metrics,
                          "elapsed_seconds": round(elapsed, 2),
                          "cpu_seconds": round(time.process_time() - cpu_start, 2),
                          "cgroup_cpu_percent": (
                              round((cgroup_cpu_end - cgroup_cpu_start) / elapsed / 10000, 2)
                              if cgroup_cpu_start is not None and cgroup_cpu_end is not None else None),
                          "cgroup_peak_bytes": int(peak_path.read_text()) if peak_path.exists() else None,
                          "python_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}),
              flush=True)


if __name__ == "__main__":
    main()
