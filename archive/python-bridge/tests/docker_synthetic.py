"""Isolated loopback-only camera and RTSP readers for temporary Docker tests."""

import json
import socket
import struct
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path

import av

from netvue_bridge.protocol import command_packet, read_packet


HOST = "127.254.9.9"
URL = "rtsp://127.0.0.1:8554/netvue2"
failures = []
connected = threading.Event()


def codec(width: int, level: str, cabac: str):
    encoder = av.CodecContext.create("libx264", "w")
    encoder.width, encoder.height, encoder.pix_fmt = width, 64, "yuv420p"
    encoder.time_base = Fraction(1, 10)
    encoder.options = {"preset": "ultrafast", "tune": "zerolatency",
                       "x264-params": "keyint=10:min-keyint=10:scenecut=0:bframes=0:"
                                      f"level={level}:cabac={cabac}"}
    encoder.open()
    return encoder


def camera(udp: socket.socket, tcp: socket.socket) -> None:
    try:
        _, sender = udp.recvfrom(65535)
        if sender[0] != "127.0.0.1":
            raise RuntimeError("Discovery sender is not loopback")
        udp.sendto(command_packet([{"A": 8, "V": 2, "C": 0, "P": [
            "fake-id", "fake-fw", HOST, 5555, "fake-key"
        ]}]), sender)
        sock, peer = tcp.accept()
        with sock:
            if peer[0] != "127.0.0.1":
                raise RuntimeError("TCP sender is not loopback")
            read_packet(sock, time.monotonic() + 3)
            sock.sendall(command_packet([{"A": 1, "V": 1, "C": 0}]))
            read_packet(sock, time.monotonic() + 3)
            sock.sendall(command_packet([{"A": 3, "V": 2, "C": 0}]))
            connected.set()
            encoder = codec(64, "3.0", "0")
            for index in range(220):
                if index == 100:
                    sock.sendall(command_packet([{"A": 3, "V": 2, "C": 0}]))
                    encoder = codec(128, "3.1", "1")
                frame = av.VideoFrame(64 if index < 100 else 128, 64, "yuv420p")
                frame.pts = index
                for plane in frame.planes:
                    plane.update(bytes(plane.buffer_size))
                for packet in encoder.encode(frame):
                    pts = index if index < 100 else index - 100
                    body = bytes([0x10 | int(packet.is_keyframe)]) + \
                        (pts * 100).to_bytes(4, "little") + bytes(packet)
                    sock.sendall(struct.pack("<IIIHH", len(body), 0, 0, 0, 201) + body)
                time.sleep(0.1)
    except (OSError, RuntimeError, av.error.FFmpegError) as exc:
        failures.append(type(exc).__name__)


def main() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp, \
         socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
        udp.settimeout(10)
        tcp.settimeout(10)
        udp.bind((HOST, 36868))
        tcp.bind((HOST, 5555))
        tcp.listen(1)
        thread = threading.Thread(target=camera, args=(udp, tcp))
        thread.start()
        Path("/data/camera.ready").touch()
        processes = []
        try:
            if not connected.wait(10):
                raise RuntimeError("Synthetic camera did not receive a connection")
            time.sleep(2)
            common = ["-v", "error", "-rtsp_transport", "tcp"]
            packets = subprocess.Popen(
                ["/ffmpeg/ffprobe", *common, "-select_streams", "v:0",
                 "-show_entries", "packet=pts_time,flags", "-show_packets",
                 "-read_intervals", "%+16", "-of", "json", URL],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append(packets)
            frames = subprocess.Popen(
                ["/ffmpeg/ffprobe", *common, "-select_streams", "v:0",
                 "-show_entries", "frame=width,height", "-show_frames",
                 "-read_intervals", "%+16", "-of", "json", URL],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append(frames)
            decoder = subprocess.Popen(
                ["/ffmpeg/ffmpeg", *common, "-i", URL, "-t", "16",
                 "-progress", "pipe:1", "-f", "null", "-"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append(decoder)
            results = [process.communicate(timeout=23) for process in processes]
            for process, (_, stderr) in zip(processes, results):
                if process.returncode:
                    raise RuntimeError(f"Reader failed: {stderr.decode(errors='replace')}")
            data = json.loads(results[0][0])
            timestamps = [float(p["pts_time"]) for p in data["packets"] if "pts_time" in p]
            dimensions = sorted({(f["width"], f["height"])
                                 for f in json.loads(results[1][0])["frames"]})
            progress = results[2][0].decode().splitlines()
            decoded = max(int(line.split("=", 1)[1]) for line in progress
                          if line.startswith("frame="))
            if len(timestamps) < 100 or not all(b > a for a, b in zip(timestamps, timestamps[1:])):
                raise RuntimeError("Insufficient or non-monotonic timestamps")
            if dimensions != [(64, 64), (128, 64)] or decoded < 100:
                raise RuntimeError("Resolution change or decoded images missing")
            print(json.dumps({"packets": len(timestamps), "decoded_frames": decoded,
                              "dimensions": dimensions,
                              "pts_span": round(timestamps[-1] - timestamps[0], 3),
                              "max_gap": round(max(b - a for a, b in zip(timestamps, timestamps[1:])), 3)}),
                  flush=True)
            thread.join(8)
            if thread.is_alive() or failures:
                raise RuntimeError(f"Synthetic camera failed: {failures}")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            udp.close()
            tcp.close()
            thread.join(10)


if __name__ == "__main__":
    main()
