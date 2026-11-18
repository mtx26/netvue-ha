"""Vérifie que la stack conserve la configuration MediaMTX de référence."""

import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class PortainerConfigTest(unittest.TestCase):
    def test_services_keep_container_security_settings(self):
        stack = yaml.safe_load((ROOT / "deployment/go-stack.yaml").read_text())

        for name, service in stack["services"].items():
            with self.subTest(service=name):
                self.assertEqual(service["user"], "10001:10001")
                self.assertTrue(service["read_only"])
                self.assertEqual(service["cap_drop"], ["ALL"])
                self.assertNotIn("ports", service)
                self.assertNotIn("volumes", service)

    def test_mediamtx_environment_matches_reference(self):
        stack = yaml.safe_load((ROOT / "deployment/go-stack.yaml").read_text())
        reference = yaml.safe_load((ROOT / "deployment/mediamtx.yml").read_text())
        service = stack["services"]["mediamtx"]
        environment = service["environment"]

        def compare(values, prefix="MTX_"):
            for key, value in values.items():
                if key == "paths":
                    continue
                name = prefix + key.upper()
                if isinstance(value, dict):
                    compare(value, name + "_")
                else:
                    if isinstance(value, list):
                        expected = ",".join(value)
                    elif isinstance(value, bool):
                        expected = str(value).lower()
                    else:
                        expected = str(value)
                    self.assertEqual(environment[name], expected, name)

        compare(reference)
        self.assertEqual(reference["paths"], {"all_others": None})
        self.assertNotIn("volumes", service)

    def test_mediamtx_only_enables_local_rtsp_tcp(self):
        config = yaml.safe_load((ROOT / "deployment/mediamtx.yml").read_text())
        self.assertTrue(config["rtsp"])
        self.assertEqual(config["rtspAddress"], "127.0.0.1:8554")
        self.assertEqual(config["rtspTransports"], ["tcp"])
        for service in ("rtmp", "hls", "webrtc", "srt", "moq", "api", "metrics", "pprof", "playback"):
            with self.subTest(service=service):
                self.assertFalse(config[service])

    def test_mediamtx_runtime_listeners_are_isolated(self):
        binary = os.environ.get("NETVUE_TEST_MEDIAMTX")
        if not binary or sys.platform != "linux":
            self.skipTest("requires Linux and NETVUE_TEST_MEDIAMTX")
        config = yaml.safe_load((ROOT / "deployment/mediamtx.yml").read_text())
        stack = yaml.safe_load((ROOT / "deployment/go-stack.yaml").read_text())
        stack_environment = stack["services"]["mediamtx"]["environment"]
        for use_stack_environment in (False, True):
            with self.subTest(stack_environment=use_stack_environment):
                with socket.socket() as reserve:
                    reserve.bind(("127.0.0.1", 0))
                    port = reserve.getsockname()[1]
                config["rtspAddress"] = f"127.0.0.1:{port}"
                environment = {key: value for key, value in os.environ.items() if not key.startswith("MTX_")}
                if use_stack_environment:
                    environment.update(stack_environment)
                    environment["MTX_RTSPADDRESS"] = config["rtspAddress"]
                with tempfile.TemporaryDirectory() as directory:
                    config_path = Path(directory) / "mediamtx.yml"
                    config_path.write_text(yaml.safe_dump({} if use_stack_environment else config))
                    process = subprocess.Popen(
                        [binary, str(config_path)], env=environment,
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    )
                    try:
                        deadline = time.monotonic() + 5
                        while True:
                            if process.poll() is not None:
                                self.fail(f"MediaMTX exited during startup: {process.stderr.read().decode()}")
                            try:
                                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                                    break
                            except OSError:
                                if time.monotonic() >= deadline:
                                    self.fail("MediaMTX RTSP listener did not become ready")
                                time.sleep(0.05)

                        socket_inodes = set()
                        for descriptor in Path(f"/proc/{process.pid}/fd").iterdir():
                            try:
                                target = os.readlink(descriptor)
                            except FileNotFoundError:
                                continue
                            if target.startswith("socket:[") and target.endswith("]"):
                                socket_inodes.add(target[8:-1])
                        listeners = set()
                        for protocol in ("tcp", "tcp6", "udp", "udp6"):
                            table = Path(f"/proc/{process.pid}/net/{protocol}").read_text().splitlines()
                            for line in table[1:]:
                                fields = line.split()
                                if fields[9] in socket_inodes and (
                                    protocol.startswith("udp") or fields[3] == "0A"
                                ):
                                    listeners.add((protocol, fields[1]))
                        self.assertEqual(listeners, {("tcp", f"0100007F:{port:04X}")})
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        finally:
                            if process.poll() is None:
                                process.kill()
                                process.wait()
                            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
