"""Continuous supervisor, fictional loopback camera and timestamp collisions."""

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from fractions import Fraction
from pathlib import Path

from netvue_bridge.protocol import ProtocolError, command_packet, read_packet


@unittest.skipUnless(os.environ.get("NETVUE_MEDIAMTX"), "requires isolated MediaMTX and PyAV")
class ContinuousTimestampIntegrationTests(unittest.TestCase):
    def test_collisions_do_not_cause_reconnection_or_reset_budget(self):
        self.check_collisions_and_reconnection(interrupt=False)

    def test_synthetic_interruption_reconnects_with_second_reservation(self):
        self.check_collisions_and_reconnection(interrupt=True)

    def check_collisions_and_reconnection(self, interrupt):
        import av

        host_suffix = (os.getpid() * 2 + int(interrupt)) % 65536
        fake_host = f"127.254.{host_suffix >> 8}.{host_suffix % 256}"
        ready = threading.Event()
        stop = threading.Event()
        errors = []
        requests = []
        with tempfile.TemporaryDirectory() as directory, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp, \
                socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            udp.bind((fake_host, 36868))
            udp.settimeout(12)
            tcp.bind((fake_host, 5555))
            tcp.listen(1)
            tcp.settimeout(12)

            def camera():
                try:
                    for attempt in range(2 if interrupt else 1):
                        _, sender = udp.recvfrom(65535)
                        requests.append("discovery")
                        udp.sendto(command_packet([{"A": 8, "V": 2, "C": 0, "P": [
                            "fake-id", "fake-fw", fake_host, 5555, "fake-key"]}]), sender)
                        sock, _ = tcp.accept()
                        with sock:
                            read_packet(sock, time.monotonic() + 5)
                            requests.append("login")
                            sock.sendall(command_packet([{"A": 1, "V": 1, "C": 0}]))
                            read_packet(sock, time.monotonic() + 5)
                            requests.append("live")
                            sock.sendall(command_packet([{"A": 3, "V": 2, "C": 0}]))
                            codec = av.CodecContext.create("libx264", "w")
                            codec.width = codec.height = 64
                            codec.pix_fmt = "yuv420p"
                            codec.time_base = Fraction(1, 10)
                            codec.options = {"preset": "ultrafast", "tune": "zerolatency",
                                             "x264-params": "keyint=10:bframes=0:scenecut=0:slices=2"}
                            codec.open()
                            for index in range(35 if interrupt and attempt == 0 else 80):
                                frame = av.VideoFrame(64, 64, "yuv420p")
                                frame.pts = index
                                for plane in frame.planes:
                                    plane.update(bytes([index % 251]) * plane.buffer_size)
                                for packet in codec.encode(frame):
                                    raw = index - 1 if index in (25, 45) else index
                                    body = bytes([0x10 | int(packet.is_keyframe)]) + \
                                        (raw * 100).to_bytes(4, "little") + bytes(packet)
                                    sock.sendall(struct.pack("<IIIHH", len(body), 0, 0, 0, 201) + body)
                                time.sleep(0.1)
                            if not interrupt or attempt == 1:
                                ready.set()
                                stop.wait(10)
                except (OSError, TimeoutError, ValueError, ProtocolError, av.error.FFmpegError) as exc:
                    errors.append(type(exc).__name__)

            config = Path(__file__).resolve().parents[1] / "deployment" / "mediamtx.yml"
            server = subprocess.Popen([os.environ["NETVUE_MEDIAMTX"], str(config)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            command = [sys.executable, "-B", "-m", "netvue_bridge.continuous",
                       "--source", "127.0.0.1", "--target", fake_host,
                       "--state-dir", directory]
            process = None
            thread = threading.Thread(target=camera)
            try:
                subprocess.run(command[:4] + ["init"] + command[4:], check=True,
                               capture_output=True, timeout=5)
                thread.start()
                # This test exercises video and budgets, not the host's cgroup configuration.
                script = ("from netvue_bridge import continuous; "
                          "continuous.resource_snapshot=lambda: "
                          "{'memory_bytes':1,'memory_limit_bytes':100,'cpu_usec':1}; ")
                if interrupt:
                    script += "continuous.reconnect_delay=lambda _:0.1; "
                script += "raise SystemExit(continuous.main())"
                run_command = [sys.executable, "-B", "-c", script, "run"] + command[4:]
                process = subprocess.Popen(run_command,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if not ready.wait(18):
                    if process.poll() is not None:
                        stdout, stderr = process.communicate(timeout=10)
                        self.fail(f"Synthetic supervisor exited: {stdout}\n{stderr}")
                    self.fail(f"Synthetic camera failed: {errors}")
                state = json.loads((Path(directory) / "budget.json").read_text())
                self.assertEqual((state["total"], state["status"], state["stage"]),
                                 (2 if interrupt else 1, "in_progress", "STREAMING"))
                self.assertEqual(requests, ["discovery", "login", "live"] * (2 if interrupt else 1))
                process.terminate()
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 130, stderr)
                line = [line for line in stdout.splitlines() if line.startswith("live_metrics: ")][-1]
                metrics = json.loads(line.split(": ", 1)[1])
                self.assertEqual(metrics["timestamp_corrections"], 2)
                self.assertEqual(metrics["published_frames"], 80)
                self.assertEqual(stdout.count("reserved attempt"), 2 if interrupt else 1)
                state = json.loads((Path(directory) / "budget.json").read_text())
                self.assertEqual((state["total"], state["status"]),
                                 (2 if interrupt else 1, "in_progress"))
            finally:
                stop.set()
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                    process.communicate(timeout=10)
                if thread.ident is not None:
                    thread.join(12)
                server.terminate()
                server.communicate(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors, str(errors))
