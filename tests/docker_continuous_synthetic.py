"""Loopback-only reconnect and video scenario for the continuous Swarm service."""

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
URL = "rtsp://127.0.0.1:28554/netvue2"
ready = threading.Event()
failures = []


def encoder(width, cabac):
    codec = av.CodecContext.create("libx264", "w")
    codec.width, codec.height, codec.pix_fmt = width, 64, "yuv420p"
    codec.time_base = Fraction(1, 10)
    codec.options = {"preset": "ultrafast", "tune": "zerolatency",
                     "x264-params": f"keyint=10:min-keyint=10:scenecut=0:bframes=0:cabac={cabac}"}
    codec.open()
    return codec


def camera(udp, tcp):
    try:
        for attempt in range(3):
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
                if attempt == 0:
                    ready.set()
                codec = encoder(64, 0)
                for index in range(180 if attempt == 0 else 30):
                    if attempt == 0 and index == 80:
                        sock.sendall(command_packet([{"A": 3, "V": 2, "C": 0}]))
                        codec = encoder(128, 1)
                    frame = av.VideoFrame(64 if attempt != 0 or index < 80 else 128, 64, "yuv420p")
                    frame.pts = index
                    for plane in frame.planes:
                        plane.update(bytes(plane.buffer_size))
                    for packet in codec.encode(frame):
                        pts = index if attempt != 0 or index < 80 else index - 80
                        body = bytes([0x10 | int(packet.is_keyframe)]) + \
                            (pts * 100).to_bytes(4, "little") + bytes(packet)
                        sock.sendall(struct.pack("<IIIHH", len(body), 0, 0, 0, 201) + body)
                    time.sleep(0.1)
            print(f"synthetic: closed connection {attempt + 1}", flush=True)
    except (OSError, RuntimeError, av.error.FFmpegError) as exc:
        failures.append(type(exc).__name__)


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp, \
         socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
        udp.settimeout(220)
        tcp.settimeout(10)
        udp.bind((HOST, 36868))
        tcp.bind((HOST, 5555))
        tcp.listen(1)
        thread = threading.Thread(target=camera, args=(udp, tcp), daemon=True)
        thread.start()
        Path("/data/camera.ready").touch()
        if not ready.wait(20):
            raise RuntimeError("Synthetic initial connection missing")
        time.sleep(2)
        common = ["-v", "error", "-rtsp_transport", "tcp"]
        probe = subprocess.Popen(
            ["/ffmpeg/ffprobe", *common, "-select_streams", "v:0", "-show_entries",
             "packet=pts_time,flags", "-show_packets", "-read_intervals", "%+12",
             "-of", "json", URL], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        decoder = subprocess.Popen(
            ["/ffmpeg/ffmpeg", *common, "-i", URL, "-t", "12",
             "-progress", "pipe:1", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        frame_probe = subprocess.Popen(
            ["/ffmpeg/ffprobe", *common, "-select_streams", "v:0", "-show_entries",
             "frame=width,height", "-show_frames", "-read_intervals", "%+12",
             "-of", "json", URL], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            p_out, p_err = probe.communicate(timeout=20)
            d_out, d_err = decoder.communicate(timeout=20)
            f_out, f_err = frame_probe.communicate(timeout=20)
            if probe.returncode or decoder.returncode or frame_probe.returncode or p_err or d_err or f_err:
                raise RuntimeError(f"Synthetic reader failed: {p_err!r} {d_err!r} {f_err!r}")
            packets = json.loads(p_out)["packets"]
            times = [float(item["pts_time"]) for item in packets if "pts_time" in item]
            frames = max(int(line.split("=", 1)[1]) for line in d_out.decode().splitlines()
                         if line.startswith("frame="))
            dimensions = sorted({(frame["width"], frame["height"])
                                 for frame in json.loads(f_out)["frames"]})
            if len(times) < 70 or frames < 70 or not all(b > a for a, b in zip(times, times[1:])):
                raise RuntimeError("Synthetic video or timestamps invalid")
            if dimensions != [(64, 64), (128, 64)]:
                raise RuntimeError("Synthetic resolution change was not decoded")
            print(json.dumps({"packets": len(times), "frames": frames,
                              "dimensions": dimensions,
                              "pts_span": round(times[-1] - times[0], 3)}), flush=True)
            thread.join(220)
            if thread.is_alive() or failures:
                raise RuntimeError(f"Synthetic camera failed: {failures}")
        finally:
            for process in (probe, decoder, frame_probe):
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
