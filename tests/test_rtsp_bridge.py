import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import MagicMock, patch

from netvue_bridge import rtsp
from netvue_bridge.check_rtsp import (
    build_reader_commands, enforce_bridge_deadline, finish_readers, partial_diagnostics,
    validate_frame_probe, validate_home_assistant_probe, validate_local_readers,
)
from netvue_bridge.discovery import DiscoveredCamera
from netvue_bridge.protocol import (
    ConnectionClosed, ProtocolError, RecoverableVideoError, command_packet, read_packet,
)
from netvue_bridge.rtsp import (
    H264Publisher, validate_local_rtsp_url,
)
from netvue_bridge.h264 import TimestampClock, annexb_nals
from netvue_bridge.stream import StreamInterrupted, receive_stream, run_session


class RtspTests(unittest.TestCase):
    def test_media_server_is_anonymous_but_loopback_only(self):
        root = Path(__file__).resolve().parents[1]
        for config in ("netvue_bridge/mediamtx.local.yml", "deployment/mediamtx.yml"):
            text = (root / config).read_text(encoding="utf-8")
            with self.subTest(config=config):
                self.assertIn("rtspAddress: 127.0.0.1:8554", text)
                self.assertNotIn("authInternalUsers", text)
        stack = (root / "deployment/stack.yaml").read_text(encoding="utf-8")
        self.assertNotRegex(stack, r"(?m)^\s+ports:")
        self.assertIn("networks: [host]", stack)
        self.assertIn("${NETVUE_MEDIAMTX_CONFIG:?set the existing MediaMTX config volume path}", stack)
        self.assertIn("${NETVUE_STATE_DIR:?set the existing persistent state volume path}", stack)
        self.assertIn(
            "${NETVUE_BRIDGE_IMAGE:?set the published Phase 17 image tag}",
            stack,
        )
        self.assertNotIn("condition: none", stack)
        self.assertEqual(stack.count("condition: on-failure"), 2)
        self.assertIn('cpus: "0.50"', stack)
        self.assertIn("memory: 512M", stack)
        self.assertIn("memory: 128M", stack)

    def test_stack_uses_portainer_variables_without_a_local_env_file(self):
        root = Path(__file__).resolve().parents[1]
        stack = (root / "deployment/stack.yaml").read_text(encoding="utf-8")
        for name in ("NETVUE_NODE", "NETVUE_SOURCE",
                     "NETVUE_MEDIAMTX_CONFIG", "NETVUE_STATE_DIR"):
            with self.subTest(name=name):
                self.assertIn(name, stack)
        self.assertNotIn("NETVUE_TARGET", stack)
        self.assertIn("netvue_bridge.manager", (root / "deployment/Dockerfile")
                      .read_text(encoding="utf-8"))
        self.assertFalse((root / "deployment/.env.example").exists())

    def test_ghcr_workflow_publishes_the_stack_image(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/publish-bridge-image.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn("ghcr.io/mtx26/netvue-ha", workflow)
        self.assertIn("--platform linux/amd64", workflow)
        self.assertIn('--tag "$IMAGE:phase17"', workflow)
        self.assertIn('--tag "$IMAGE:sha-${GITHUB_SHA}"', workflow)
        self.assertIn("--push .", workflow)
        stack = (root / "deployment/stack.yaml").read_text(encoding="utf-8")
        self.assertIn("${NETVUE_BRIDGE_IMAGE:?set the published Phase 17 image tag}", stack)

    def test_check_rtsp_help_keeps_cli_options(self):
        result = subprocess.run([sys.executable, "-B", "-m", "netvue_bridge.check_rtsp", "--help"],
                                capture_output=True, text=True, timeout=5, check=True)
        for option in ("--source", "--target", "--state-dir", "--mediamtx", "--ffprobe", "--ffmpeg",
                       "--duration", "--probe-duration", "--reader-delay", "--bridge-timeout",
                       "--home-assistant-check", "--frame-probe"):
            with self.subTest(option=option):
                self.assertIn(option, result.stdout)

    def test_stream_module_cannot_start_an_unsupervised_session(self):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "netvue_bridge.stream", "--help"],
            capture_output=True, text=True, timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("internal", result.stderr)

    def test_reader_commands_keep_optional_checks_and_local_url(self):
        args = types.SimpleNamespace(ffprobe="probe", ffmpeg="decoder", duration=10,
                                     probe_duration=3, frame_probe=False)
        basic = dict(build_reader_commands(args, None))
        self.assertEqual(list(basic), ["ffprobe", "ffmpeg"])
        self.assertEqual(basic["ffprobe"][-1], "rtsp://127.0.0.1:8554/netvue2")
        self.assertIn("%+3", basic["ffprobe"])
        self.assertFalse(any("@" in arg for command in basic.values() for arg in command))
        args.frame_probe = True
        extended = dict(build_reader_commands(args, "fake-ha"))
        self.assertEqual(list(extended), ["ffprobe", "ffmpeg", "frame_probe",
                                          "home_assistant_ffprobe", "home_assistant_ffmpeg"])
        self.assertEqual(extended["home_assistant_ffprobe"][:7],
                         ["docker", "exec", "fake-ha", "timeout", "-k", "2", "18"])
        self.assertEqual(extended["home_assistant_ffprobe"][8:], basic["ffprobe"][1:])

    def test_frame_probe_checks_decoded_dimensions(self):
        data = json.dumps({"frames": [{"width": 128, "height": 64},
                                      {"width": 64, "height": 64},
                                      {"width": 64, "height": 64}, {}]}).encode()
        self.assertEqual(validate_frame_probe(data, b"", 0), [(64, 64), (128, 64)])
        with self.assertRaisesRegex(RuntimeError, "did not decode"):
            validate_frame_probe(b'{"frames":[]}', b"", 0)
        with self.assertRaisesRegex(RuntimeError, "error lines=1"):
            validate_frame_probe(b"", b"error\n", 1)

    def test_home_assistant_probe_checks_matching_stream(self):
        data = json.dumps({"streams": [{"codec_name": "h264", "width": 64, "height": 64}],
                           "packets": [{}, {}]}).encode()
        local = {"width": 64, "height": 64}
        self.assertEqual(validate_home_assistant_probe(data, b"", 0, local),
                         {"codec": "h264", "width": 64, "height": 64,
                          "packets": 2, "ffprobe_error_lines": 0})
        with self.assertRaisesRegex(RuntimeError, "could not verify"):
            validate_home_assistant_probe(data, b"", 0, {"width": 128, "height": 64})
        with self.assertRaisesRegex(RuntimeError, "error lines=1"):
            validate_home_assistant_probe(data, b"error\n", 1, local)

    def test_local_reader_validation_preserves_summary_and_thresholds(self):
        probe = json.dumps({
            "streams": [{"codec_name": "h264", "width": 64, "height": 32}],
            "packets": [{"pts_time": "0.0", "flags": "K"},
                        {"pts_time": "1.0", "flags": ""}],
        }).encode()
        summary = validate_local_readers(probe, b"warning\n", b"frame=2\n", b"",
                                         1.0, 0.125, 0.5)
        self.assertEqual(summary, {
            "codec": "h264", "width": 64, "height": 32, "packets": 2,
            "keyframes": 1, "timestamped_packets": 2, "decoded_frames": 2,
            "first_pts_seconds": 0.0, "last_pts_seconds": 1.0,
            "timestamp_span_seconds": 1.0, "max_timestamp_gap_seconds": 1.0,
            "fps_from_timestamps": 1.0, "startup_seconds": 0.125,
            "reader_delay_seconds": 0.5, "ffprobe_error_lines": 1,
            "ffmpeg_error_lines": 0,
        })

    def test_local_reader_validation_rejects_invalid_video(self):
        def probe(codec, timestamps, key=True):
            return json.dumps({
                "streams": [{"codec_name": codec}],
                "packets": [{"pts_time": str(value), "flags": "K" if key and index == 0 else ""}
                            for index, value in enumerate(timestamps)],
            }).encode()

        for data, frames, duration, error in (
            (probe("hevc", [0, 1]), b"frame=2\n", 1, "H.264"),
            (probe("h264", [0, 0]), b"frame=2\n", 1, "non-monotonic"),
            (probe("h264", [0, 1.01]), b"frame=2\n", 1, "continuity"),
            (probe("h264", [0, 0.7]), b"frame=2\n", 1, "continuity"),
            (probe("h264", [0, 1], key=False), b"frame=2\n", 1, "keyframe"),
            (probe("h264", [0, 1]), b"frame=1\n", 1, "keyframe"),
        ):
            with self.subTest(error=error, data=data, frames=frames):
                with self.assertRaisesRegex(RuntimeError, error):
                    validate_local_readers(data, b"", frames, b"", duration, 1, 0)

    def test_bridge_watchdog_stops_overdue_process(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertFalse(enforce_bridge_deadline(process, time.monotonic() + 1))
            self.assertTrue(enforce_bridge_deadline(process, time.monotonic() - 1))
            process.wait(timeout=3)
            self.assertNotEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)

    def test_partial_reader_diagnostics_are_explicit(self):
        outputs = {
            "ffprobe": (json.dumps({"streams": [{"codec_name": "h264", "width": 64,
                                                   "height": 64}],
                                     "packets": [{"flags": "K__"}]}).encode(), b""),
            "home_assistant_ffprobe": (b"{", b"failure\n"),
            "ffmpeg": (b"frame=4\n", b""),
        }
        summary = partial_diagnostics(outputs)
        self.assertEqual(summary["ffprobe"]["keyframes"], 1)
        self.assertTrue(summary["home_assistant_ffprobe"]["incomplete_json"])
        self.assertEqual(summary["ffmpeg"]["decoded_frames"], 4)

    def test_interrupted_readers_are_drained_after_termination(self):
        processes = {}
        outputs = {}
        workers = {}
        try:
            for name in ("ffprobe", "ffmpeg", "home_assistant_ffprobe", "home_assistant_ffmpeg"):
                payload = ('{"packets":[{"pts_time":"1.0","flags":"K"}]}'
                           if "ffprobe" in name else "frame=4")
                script = ("import sys,time; "
                          f"print({payload!r}, flush=True); "
                          "print('ready', file=sys.stderr, flush=True); time.sleep(30)")
                process = subprocess.Popen([sys.executable, "-u", "-c", script],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                processes[name] = process
                self.assertEqual(process.stderr.readline().strip(), b"ready")
                worker = threading.Thread(target=lambda n=name, p=process: outputs.update(
                    {n: p.communicate()}))
                workers[name] = worker
                worker.start()
            finish_readers(processes, workers)
            summary = partial_diagnostics(outputs)
            for name in processes:
                self.assertIsNotNone(processes[name].returncode)
                self.assertFalse(workers[name].is_alive())
                self.assertTrue(summary[name]["connection_verified"])
        finally:
            for process in processes.values():
                if process.poll() is None:
                    process.kill()
                    process.wait()
            for worker in workers.values():
                worker.join(timeout=3)
            for process in processes.values():
                for pipe in (process.stdout, process.stderr):
                    if pipe is not None and not pipe.closed:
                        pipe.close()

    def test_partial_probe_json_preserves_complete_packets(self):
        data = (b'{"packets":[{"pts_time":"0.1","flags":"K"},'
                b'{"pts_time":"0.2","flags":"__"},{"pts_time":')
        summary = partial_diagnostics({"ffprobe": (data, b"")})["ffprobe"]
        self.assertTrue(summary["incomplete_json"])
        self.assertEqual(summary["packets"], 2)
        self.assertEqual(summary["keyframes"], 1)
        self.assertEqual(summary["timestamp_span_seconds"], 0.1)

    def test_only_local_rtsp_is_allowed(self):
        validate_local_rtsp_url("rtsp://127.0.0.1:8554/netvue2")
        for url in ("rtsp://example.com:8554/netvue2", "rtsp://0.0.0.0:8554/x",
                    "rtsp://127.0.0.1/x", "rtsp://user@127.0.0.1:8554/x"):
            with self.assertRaises(ValueError):
                validate_local_rtsp_url(url)

    def test_nal_boundaries_and_wraparound(self):
        data = b"\x00\x00\x00\x01\x67abc\x00\x00\x01\x68def"
        self.assertEqual(annexb_nals(data), [b"\x67abc", b"\x68def"])
        with self.assertRaises(ProtocolError):
            annexb_nals(b"not annex b")
        clock = TimestampClock()
        self.assertEqual([clock.update(n) for n in (0xFFFFFFFE, 1, 21)], [0, 3, 23])
        with self.assertRaises(ProtocolError):
            clock.update(20)
        clock = TimestampClock()
        self.assertEqual([clock.update(n) for n in (100, 200, 500)], [0, 100, 400])
        with self.assertRaisesRegex(ProtocolError, "Duplicate"):
            clock.update(500)
        with self.assertRaisesRegex(ProtocolError, "discontinuity"):
            clock.update(12000)

    def test_missing_channel_confirmation_is_rejected(self):
        left, right = socket.socketpair()
        try:
            body = b"\x11" + (0).to_bytes(4, "little") + b"\x00\x00\x01\x65x"
            right.sendall(struct.pack("<IIIHH", len(body), 0, 0, 0, 201) + body)
            with self.assertRaisesRegex(ProtocolError, "before LIVE confirmation"):
                receive_stream(left, object(), 1)
        finally:
            left.close()
            right.close()

    def test_waits_for_parameters_and_idr_and_bounds_startup(self):
        decoder = MagicMock()
        decoder.decode.return_value = []
        decoder.has_b_frames = False
        fake_av = types.SimpleNamespace(
            CodecContext=types.SimpleNamespace(create=MagicMock(return_value=decoder)),
            Packet=lambda data: data,
            error=types.SimpleNamespace(FFmpegError=ValueError),
        )
        with patch.dict(sys.modules, {"av": fake_av}):
            publisher = H264Publisher("rtsp://127.0.0.1:8554/netvue2")
            publisher.push(b"\x00\x00\x01\x67sps", 0)
            publisher.push(b"\x00\x00\x01\x68pps", 0)
            publisher.push(b"\x00\x00\x01\x41nonkey", 0)
            self.assertFalse(publisher.started)
            publisher.push(b"\x00\x00\x01\x65idr", 100)
            self.assertTrue(publisher.started)
            self.assertTrue(publisher.pending[0][0].startswith(
                b"\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x68pps"))
            for index in range(1, 128):
                publisher.push(b"\x00\x00\x01\x41next", 100 + index)
            with self.assertRaisesRegex(ProtocolError, "buffer limit"):
                publisher.push(b"\x00\x00\x01\x41next", 228)
            publisher.close()
            self.assertEqual(publisher.pending_bytes, 0)

    def test_decoder_applies_pixel_limit_before_any_h264_decode(self):
        decoder = MagicMock()
        fake_av = types.SimpleNamespace(
            CodecContext=types.SimpleNamespace(create=MagicMock(return_value=decoder)),
            error=types.SimpleNamespace(FFmpegError=ValueError),
        )
        with patch.dict(sys.modules, {"av": fake_av}):
            publisher = H264Publisher("rtsp://127.0.0.1:8554/netvue2")
            self.assertEqual(decoder.options, {"max_pixels": str(rtsp.MAX_H264_PIXELS)})
            self.assertEqual(decoder.decode.call_count, 0)
            publisher.close()

    def test_close_releases_buffers_even_if_output_close_fails(self):
        fake_av = types.SimpleNamespace(
            CodecContext=types.SimpleNamespace(create=MagicMock()),
            Packet=lambda data: data, error=types.SimpleNamespace(FFmpegError=ValueError),
        )
        with patch.dict(sys.modules, {"av": fake_av}):
            publisher = H264Publisher("rtsp://127.0.0.1:8554/netvue2")
            publisher.output = MagicMock()
            publisher.output.close.side_effect = ValueError("synthetic close error")
            publisher.pending.append((b"frame", 0, True))
            publisher.pending_bytes = 5
            publisher.change_pending.append((b"frame", 0, True))
            publisher.change_pending_bytes = 5
            publisher._collision_frames.append((b"frame", 0.0))
            publisher._collision_bytes = 5
            publisher._collision_started = 0.0
            publisher._last_access_unit = b"frame"
            with self.assertRaisesRegex(ValueError, "synthetic close error"):
                publisher.close()
            self.assertIsNone(publisher.output)
            self.assertEqual((publisher.pending, publisher.change_pending, publisher._collision_frames),
                             ([], [], []))
            self.assertEqual((publisher.pending_bytes, publisher.change_pending_bytes,
                              publisher._collision_bytes), (0, 0, 0))
            self.assertIsNone(publisher._last_access_unit)
            self.assertIsNone(publisher._collision_started)

    def test_parameter_change_waits_for_idr_and_handles_dimensions(self):
        frame = types.SimpleNamespace(width=64, height=64)
        decoder = MagicMock()
        decoder.decode.return_value = [frame]
        decoder.has_b_frames = False
        fake_av = types.SimpleNamespace(
            CodecContext=types.SimpleNamespace(create=MagicMock(return_value=decoder)),
            Packet=lambda data: data,
            error=types.SimpleNamespace(FFmpegError=ValueError),
        )
        with patch.dict(sys.modules, {"av": fake_av}):
            publisher = H264Publisher("rtsp://127.0.0.1:8554/netvue2")
            publisher.started = True
            publisher.sps, publisher.pps = b"\x67old", b"\x68pps"
            publisher.width, publisher.height = 64, 64
            publisher.output = MagicMock()
            with patch.object(publisher, "_mux") as mux:
                publisher.push(b"\x00\x00\x01\x67new\x00\x00\x01\x41delta", 0)
                self.assertTrue(publisher.config_changed)
                mux.assert_not_called()
                publisher.push(b"\x00\x00\x01\x65idr", 100)
                self.assertFalse(publisher.config_changed)
                self.assertEqual(publisher.parameter_updates, 1)
                self.assertTrue(mux.call_args.args[0].startswith(
                    b"\x00\x00\x00\x01\x67new\x00\x00\x00\x01\x68pps"))
                frame.width = 128
                publisher.push(b"\x00\x00\x01\x67next\x00\x00\x01\x65idr", 200)
                self.assertEqual((publisher.width, publisher.height), (128, 64))
                decoder.has_b_frames = True
                with self.assertRaisesRegex(ProtocolError, "B-frame"):
                    publisher.push(b"\x00\x00\x01\x67other\x00\x00\x01\x65idr", 300)
            publisher.close()

    def test_acknowledged_reconfiguration_rebases_zero_timestamp_only(self):
        frame = types.SimpleNamespace(width=64, height=64)
        decoder = MagicMock()
        decoder.decode.return_value = [frame]
        decoder.has_b_frames = False
        fake_av = types.SimpleNamespace(
            CodecContext=types.SimpleNamespace(create=MagicMock(return_value=decoder)),
            Packet=lambda data: data,
            error=types.SimpleNamespace(FFmpegError=ValueError),
        )
        with patch.dict(sys.modules, {"av": fake_av}):
            for acknowledged, timestamp in ((False, 0), (True, 1), (True, 0)):
                publisher = H264Publisher("rtsp://127.0.0.1:8554/netvue2")
                publisher.started = True
                publisher.sps, publisher.pps = b"\x67old", b"\x68pps"
                publisher.width, publisher.height = 64, 64
                publisher.output = MagicMock()
                for value in (0, 5000, 10808):
                    publisher.clock.update(value)
                publisher.last_clock_time = 100.0
                if acknowledged:
                    publisher.note_repeated_live_confirmation()
                with patch.object(publisher, "_mux") as mux, \
                     patch("netvue_bridge.rtsp.time.monotonic", return_value=100.12):
                    data = b"\x00\x00\x01\x67new\x00\x00\x01\x65idr"
                    if not acknowledged or timestamp != 0:
                        with self.assertRaisesRegex(ProtocolError, "moved backwards"):
                            publisher.push(data, timestamp)
                    else:
                        publisher.push(data, timestamp)
                        self.assertEqual(mux.call_args.args[1], 10928)
                        self.assertEqual(publisher.safe_metrics()["timestamp_resets"], 1)
                        self.assertEqual(publisher.clock.previous, 0)
                publisher.close()

    def test_reconfirmed_zero_pts_without_parameter_change_is_rejected(self):
        fake_av = types.SimpleNamespace(
            CodecContext=types.SimpleNamespace(create=MagicMock()),
            Packet=lambda data: data, error=types.SimpleNamespace(FFmpegError=ValueError),
        )
        with patch.dict(sys.modules, {"av": fake_av}):
            publisher = H264Publisher("rtsp://127.0.0.1:8554/netvue2")
            publisher.clock.update(100)
            publisher.last_clock_time = 100.0
            publisher.note_repeated_live_confirmation()
            with self.assertRaisesRegex(ProtocolError, "moved backwards"):
                publisher._timestamp(0, 100.1, True)
            publisher.close()

    def test_change_recovers_from_decode_error_and_waits_for_next_idr(self):
        frame = types.SimpleNamespace(width=64, height=64)
        decoder = MagicMock()
        decoder.has_b_frames = False
        decoder.decode.side_effect = [ValueError("synthetic"), [], [frame]]
        fake_av = types.SimpleNamespace(
            CodecContext=types.SimpleNamespace(create=MagicMock(return_value=decoder)),
            Packet=lambda data: data, error=types.SimpleNamespace(FFmpegError=ValueError),
        )
        with patch.dict(sys.modules, {"av": fake_av}):
            publisher = H264Publisher("rtsp://127.0.0.1:8554/netvue2")
            publisher.started = True
            publisher.sps, publisher.pps = b"\x67sps", b"\x68old"
            publisher.width, publisher.height = 64, 64
            publisher.output = MagicMock()
            with patch.object(publisher, "_mux") as mux, patch("builtins.print"):
                publisher.push(b"\x00\x00\x01\x68new\x00\x00\x01\x65idr", 100)
                self.assertEqual((publisher.pps_changes, publisher.decode_errors), (1, 1))
                publisher.push(b"\x00\x00\x01\x65idr", 200)
                self.assertEqual(mux.call_count, 0)
                publisher.push(b"\x00\x00\x01\x41delta", 300)
                self.assertEqual(mux.call_count, 2)
                self.assertEqual(publisher.parameter_updates, 1)
            publisher.close()

    def test_missing_idr_is_reported_separately(self):
        fake_av = types.SimpleNamespace(
            CodecContext=types.SimpleNamespace(create=MagicMock()), Packet=lambda data: data,
            error=types.SimpleNamespace(FFmpegError=ValueError),
        )
        with patch.dict(sys.modules, {"av": fake_av}), \
             patch("netvue_bridge.rtsp.time.monotonic", side_effect=[0, 0, 6]):
            publisher = H264Publisher("rtsp://127.0.0.1:8554/netvue2")
            publisher.sps, publisher.pps = b"\x67sps", b"\x68pps"
            publisher.push(b"\x00\x00\x01\x41delta", 0)
            with self.assertRaisesRegex(ProtocolError, "IDR missing"):
                publisher.push(b"\x00\x00\x01\x41delta", 100)
            publisher.close()

    @unittest.skipUnless(os.environ.get("NETVUE_MEDIAMTX"),
                         "requires PyAV and synthetic H.264 encoder")
    def test_b_frames_are_rejected_before_publication(self):
        import av

        codec = av.CodecContext.create("libx264", "w")
        codec.width, codec.height, codec.pix_fmt = 64, 64, "yuv420p"
        codec.time_base = Fraction(1, 10)
        codec.options = {"preset": "medium", "x264-params": "keyint=10:bframes=2"}
        codec.open()
        packets = []
        for index in range(20):
            frame = av.VideoFrame(64, 64, "yuv420p")
            frame.pts = index
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            packets.extend(codec.encode(frame))
        packets.extend(codec.encode(None))
        self.assertTrue(any(packet.pts != packet.dts for packet in packets))
        publisher = H264Publisher("rtsp://127.0.0.1:8554/netvue2")
        try:
            with self.assertRaisesRegex(ProtocolError, "B-frame"):
                for index, packet in enumerate(packets):
                    publisher.push(bytes(packet), index * 100)
            self.assertEqual(publisher.published_frames, 0)
        finally:
            publisher.close()

    def test_session_reports_transient_failure_without_retrying(self):
        camera = DiscoveredCamera("192.0.2.1", 5555, "fake-key")
        publisher = MagicMock()
        publisher.safe_metrics.return_value = {}
        with patch("netvue_bridge.stream.discover", return_value=camera), \
             patch("netvue_bridge.stream.connect", return_value=MagicMock()), \
             patch("netvue_bridge.stream.authenticate", return_value=0) as auth, \
             patch("netvue_bridge.stream.H264Publisher", return_value=publisher), \
             patch("netvue_bridge.stream.receive_stream",
                   side_effect=StreamInterrupted("stalled")), \
             patch("builtins.print"):
            self.assertEqual(run_session("source", "target", "rtsp://127.0.0.1:8554/netvue2",
                                         10), "live")
            auth.assert_called_once()
            publisher.close.assert_called_once()
        with patch("netvue_bridge.stream.discover", return_value=camera), \
             patch("netvue_bridge.stream.connect", return_value=MagicMock()), \
             patch("netvue_bridge.stream.authenticate", return_value=5) as auth, \
             patch("builtins.print"):
            self.assertEqual(run_session("source", "target", "rtsp://127.0.0.1:8554/netvue2",
                                         10), "blocked")
            auth.assert_called_once()
        with patch("netvue_bridge.stream.discover", return_value=camera), \
             patch("netvue_bridge.stream.connect", return_value=MagicMock()), \
             patch("netvue_bridge.stream.authenticate",
                   side_effect=ConnectionClosed("closed")) as auth, \
             patch("builtins.print"):
            self.assertEqual(run_session("source", "target", "rtsp://127.0.0.1:8554/netvue2",
                                         10), "blocked")
            auth.assert_called_once()

    def test_session_reports_discovery_and_connection_failures(self):
        camera = DiscoveredCamera("192.0.2.1", 5555, "fake-key")
        with patch("netvue_bridge.stream.discover", side_effect=TimeoutError), \
             patch("builtins.print"):
            self.assertEqual(run_session("source", "target", "rtsp://127.0.0.1:8554/netvue2",
                                         10), "discovery")
        with patch("netvue_bridge.stream.discover", return_value=camera), \
             patch("netvue_bridge.stream.connect", side_effect=OSError("offline")), \
             patch("builtins.print"):
            self.assertEqual(run_session("source", "target", "rtsp://127.0.0.1:8554/netvue2",
                                         10), "connect")

    def test_recoverable_video_is_distinct_from_critical_protocol_error(self):
        camera = DiscoveredCamera("192.0.2.1", 5555, "fake-key")
        publisher = MagicMock()
        publisher.safe_metrics.return_value = {}
        with patch("netvue_bridge.stream.discover", return_value=camera), \
             patch("netvue_bridge.stream.connect", return_value=MagicMock()), \
             patch("netvue_bridge.stream.authenticate", return_value=0), \
             patch("netvue_bridge.stream.H264Publisher", return_value=publisher), \
             patch("netvue_bridge.stream.receive_stream",
                   side_effect=[RecoverableVideoError("duplicate"), ProtocolError("invalid")]), \
             patch("builtins.print"):
            self.assertEqual(run_session("source", "target", "rtsp://127.0.0.1:8554/netvue2",
                                         10), "video")
            self.assertEqual(run_session("source", "target", "rtsp://127.0.0.1:8554/netvue2",
                                         10), "blocked")

    def test_interruption_closes_socket_and_publisher(self):
        camera = DiscoveredCamera("192.0.2.1", 5555, "fake-key")
        publisher = MagicMock()
        publisher.safe_metrics.return_value = {}
        left, right = socket.socketpair()
        try:
            with patch("netvue_bridge.stream.discover", return_value=camera), \
                 patch("netvue_bridge.stream.connect", return_value=left), \
                 patch("netvue_bridge.stream.authenticate", return_value=0), \
                 patch("netvue_bridge.stream.H264Publisher", return_value=publisher), \
                 patch("netvue_bridge.stream.receive_stream", side_effect=KeyboardInterrupt), \
                 patch("builtins.print"):
                with self.assertRaises(KeyboardInterrupt):
                        run_session("source", "target", "rtsp://127.0.0.1:8554/netvue2", 10)
            self.assertEqual(left.fileno(), -1)
            publisher.close.assert_called_once()
        finally:
            left.close()
            right.close()

    @unittest.skipUnless(os.environ.get("NETVUE_MEDIAMTX") and os.environ.get("NETVUE_FFPROBE"),
                         "requires portable MediaMTX, FFprobe, and PyAV")
    def test_synthetic_camera_to_rtsp_ffprobe(self):
        self.check_synthetic_camera_to_rtsp("netvue_bridge", "mediamtx.local.yml")

    @unittest.skipUnless(os.environ.get("NETVUE_MEDIAMTX") and os.environ.get("NETVUE_FFPROBE"),
                         "requires portable MediaMTX, FFprobe, and PyAV")
    def test_deployment_config_with_synthetic_camera(self):
        self.check_synthetic_camera_to_rtsp(
            "deployment", "mediamtx.yml", "netvue_192_168_10_2"
        )

    def check_synthetic_camera_to_rtsp(self, directory, filename, stream_name="netvue2"):
        import av

        config = Path(__file__).resolve().parents[1] / directory / filename
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            port = port_socket.getsockname()[1]
        temp = tempfile.TemporaryDirectory()
        test_config = Path(temp.name) / "mediamtx.yml"
        test_config.write_text(config.read_text().replace(":8554", f":{port}"))
        url = f"rtsp://127.0.0.1:{port}/{stream_name}"
        server = subprocess.Popen([os.environ["NETVUE_MEDIAMTX"], str(test_config)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        left, right = socket.socketpair()
        failures = []
        publisher = H264Publisher(url)

        def produce():
            try:
                read_packet(right, time.monotonic() + 3)
                right.sendall(command_packet([{"A": 3, "V": 2, "C": 0}]))
                codec = av.CodecContext.create("libx264", "w")
                codec.width, codec.height, codec.pix_fmt = 64, 64, "yuv420p"
                codec.time_base = Fraction(1, 10)
                codec.options = {"preset": "ultrafast", "tune": "zerolatency",
                                 "x264-params": "keyint=10:min-keyint=10:scenecut=0:bframes=0"}
                codec.open()
                collision = bool(os.environ.get("NETVUE_TEST_COLLISION"))
                for index in range(80 if collision else 55):
                    frame = av.VideoFrame(64, 64, "yuv420p")
                    frame.pts = index
                    for plane in frame.planes:
                        plane.update(bytes([index % 251 if collision else 0]) * plane.buffer_size)
                    for packet in codec.encode(frame):
                        pts_index = index
                        if collision and index in (25, 35):
                            pts_index -= 1
                        elif collision and index == 36:
                            pts_index -= 2
                        body = bytes([0x10 | int(packet.is_keyframe)]) + \
                            (pts_index * 100).to_bytes(4, "little") + bytes(packet)
                        right.sendall(struct.pack("<IIIHH", len(body), 0, 0, 0, 201) + body)
                    time.sleep(0.1)
                time.sleep(1)
            except (OSError, ProtocolError, TimeoutError) as exc:
                failures.append(type(exc).__name__)

        def consume():
            try:
                receive_stream(left, publisher, 9 if os.environ.get("NETVUE_TEST_COLLISION") else 6)
            except (OSError, ProtocolError, TimeoutError, av.error.FFmpegError) as exc:
                failures.append(type(exc).__name__ + ": " + str(exc))

        writer = threading.Thread(target=produce)
        reader = threading.Thread(target=consume)
        player = None
        try:
            deadline = time.monotonic() + 5
            while True:
                if server.poll() is not None:
                    self.fail("MediaMTX failed to start: " + server.stderr.read().decode())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("MediaMTX did not become ready")
                    time.sleep(0.05)
            writer.start()
            reader.start()
            while publisher.published_frames < 1 and reader.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertGreater(publisher.published_frames, 0, str(failures))
            ffmpeg = str(Path(os.environ["NETVUE_FFPROBE"]).with_name("ffmpeg"))
            reader_duration = "4" if os.environ.get("NETVUE_TEST_COLLISION") else "2"
            player = subprocess.Popen(
                [ffmpeg, "-v", "error", "-rtsp_transport", "tcp", "-i", url,
                 "-t", reader_duration, "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            packet_count = 40 if os.environ.get("NETVUE_TEST_COLLISION") else 20
            probe = subprocess.run(
                [os.environ["NETVUE_FFPROBE"], "-v", "error", "-rtsp_transport", "tcp",
                 "-select_streams", "v:0", "-show_entries",
                 "stream=codec_name,width,height:packet=pts_time,flags",
                 "-show_streams", "-show_packets", "-read_intervals",
                 "%+#" + str(packet_count), "-of", "json", url],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=8, check=True,
            )
            result = json.loads(probe.stdout)
            stream = result["streams"][0]
            packets = result["packets"]
            self.assertEqual((stream["codec_name"], stream["width"], stream["height"]),
                             ("h264", 64, 64))
            self.assertTrue(all("pts_time" in p for p in packets[1:]), str(packets[:3]))
            times = [float(p["pts_time"]) for p in packets if "pts_time" in p]
            self.assertGreaterEqual(len(times), 10)
            if os.environ.get("NETVUE_TEST_COLLISION"):
                self.assertGreaterEqual(len(times), 35)
            self.assertTrue(all(b > a for a, b in zip(times, times[1:])))
            self.assertTrue(all(abs((b - a) - 0.1) < 0.005
                                for a, b in zip(times, times[1:])))
            self.assertTrue(any("K" in p["flags"] for p in packets))
            _, player_error = player.communicate(timeout=6)
            self.assertEqual(player.returncode, 0, player_error.decode())
            reader.join(10)
            self.assertFalse(reader.is_alive())
            self.assertFalse(failures, str(failures))
            self.assertGreaterEqual(publisher.published_frames, 50)
            if os.environ.get("NETVUE_TEST_COLLISION"):
                self.assertEqual(publisher.safe_metrics()["timestamp_corrections"], 3)
            print(f"synthetic RTSP: {len(times)} packets; {publisher.published_frames} "
                  f"published; {publisher.key_frames} keyframes")
        finally:
            if player is not None:
                if player.poll() is None:
                    player.terminate()
                try:
                    player.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    player.kill()
                    player.communicate(timeout=3)
            left.close()
            right.close()
            if writer.ident is not None:
                writer.join(10)
            if reader.ident is not None:
                reader.join(10)
            publisher.close()
            if server.poll() is None:
                server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=3)
            server.stderr.close()
            temp.cleanup()

    @unittest.skipUnless(os.name == "posix" and os.environ.get("NETVUE_MEDIAMTX")
                         and os.environ.get("NETVUE_FFPROBE")
                         and all(Path("/sys/fs/cgroup", item).exists()
                                 for item in ("memory.current", "memory.max", "cpu.stat")),
                         "requires portable RTSP tools and cgroup v2 resource files")
    def test_bounded_check_with_synthetic_camera(self):
        import av

        duration = int(os.environ.get("NETVUE_TEST_DURATION", "10"))
        reader_delay = 3 if duration >= 30 else 0
        probe_duration = int(os.environ.get("NETVUE_TEST_PROBE_DURATION",
                                            "20" if duration >= 30 else "3"))
        bridge_timeout = os.environ.get("NETVUE_TEST_BRIDGE_TIMEOUT")
        reconfigure = bool(os.environ.get("NETVUE_TEST_RECONFIG"))
        resize = bool(os.environ.get("NETVUE_TEST_RESIZE"))
        interrupt = bool(os.environ.get("NETVUE_TEST_INTERRUPT"))
        pps_change = bool(os.environ.get("NETVUE_TEST_PPS"))
        reset_pts = bool(os.environ.get("NETVUE_TEST_PTS_RESET"))
        drop_frame = bool(os.environ.get("NETVUE_TEST_DROP"))
        if reset_pts and not reconfigure:
            self.fail("Synthetic timestamp reset requires reconfiguration")
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(udp.close)
        self.addCleanup(tcp.close)
        fake_host = f"127.254.{(os.getpid() >> 8) % 256}.{os.getpid() % 256}"
        udp.settimeout(4)
        tcp.settimeout(4)
        udp.bind((fake_host, 36868))
        tcp.bind((fake_host, 5555))
        tcp.listen(1)
        counts = []
        failures = []

        def camera():
            try:
                _, sender = udp.recvfrom(65535)
                udp.sendto(command_packet([{"A": 8, "V": 2, "C": 0, "P": [
                    "fake-id", "fake-fw", fake_host, 5555, "fake-key"
                ]}]), sender)
                sock, _ = tcp.accept()
                with sock:
                    read_packet(sock, time.monotonic() + 3)
                    counts.append("login")
                    sock.sendall(command_packet([{"A": 1, "V": 1, "C": 0}]))
                    read_packet(sock, time.monotonic() + 3)
                    sock.sendall(command_packet([{"A": 3, "V": 2, "C": 0}]))
                    def make_codec(level, width, cabac):
                        codec = av.CodecContext.create("libx264", "w")
                        codec.width, codec.height, codec.pix_fmt = width, 64, "yuv420p"
                        codec.time_base = Fraction(1, 10)
                        codec.options = {"preset": "ultrafast", "tune": "zerolatency",
                                         "x264-params": "keyint=10:min-keyint=10:scenecut=0:"
                                                        "bframes=0:level=" + level + ":cabac=" + cabac}
                        codec.open()
                        return codec

                    width = 64
                    codec = make_codec("3.0", width, "0")
                    for index in range((duration - 1) * 10):
                        if interrupt and index == 25:
                            return
                        if reconfigure and index == 100:
                            if reset_pts:
                                sock.sendall(command_packet([{"A": 3, "V": 2, "C": 0}]))
                            width = 128 if resize else 64
                            codec = make_codec("3.1", width, "1" if pps_change else "0")
                        frame = av.VideoFrame(width, 64, "yuv420p")
                        frame.pts = index
                        for plane in frame.planes:
                            plane.update(bytes(plane.buffer_size))
                        for packet in codec.encode(frame):
                            if drop_frame and index == 15:
                                continue
                            pts_index = index - 100 if reset_pts and index >= 100 else index
                            body = bytes([0x10 | int(packet.is_keyframe)]) + \
                                (pts_index * 100).to_bytes(4, "little") + bytes(packet)
                            sock.sendall(struct.pack("<IIIHH", len(body), 0, 0, 0, 201) + body)
                        time.sleep(0.1)
                    time.sleep(1.5)
            except (OSError, ProtocolError, TimeoutError, av.error.FFmpegError) as exc:
                failures.append(type(exc).__name__)

        thread = threading.Thread(target=camera)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as state_dir:
                from netvue_bridge.budget import Budget

                with Budget(Path(state_dir), "127.0.0.1", fake_host) as budget:
                    budget.initialize()
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "netvue_bridge.check_rtsp",
                     "--source", "127.0.0.1", "--target", fake_host,
                     "--state-dir", state_dir,
                     "--mediamtx", os.environ["NETVUE_MEDIAMTX"],
                     "--ffprobe", os.environ["NETVUE_FFPROBE"],
                     "--ffmpeg", str(Path(os.environ["NETVUE_FFPROBE"]).with_name("ffmpeg")),
                     "--duration", str(duration), "--reader-delay", str(reader_delay),
                     "--probe-duration", str(probe_duration)]
                    + (["--home-assistant-check"] if os.environ.get("NETVUE_TEST_HA") else [])
                    + (["--bridge-timeout", bridge_timeout] if bridge_timeout else [])
                    + (["--frame-probe"] if resize else []),
                    capture_output=True, text=True, timeout=duration + 12,
                )
            if interrupt:
                self.assertNotEqual(result.returncode, 0, result.stdout)
                line = next(line for line in result.stdout.splitlines() if line.startswith("readers: "))
                readers = json.loads(line.split(": ", 1)[1])
                self.assertGreater(readers["ffprobe"]["packets"], 0)
                self.assertGreater(readers["ffmpeg"]["decoded_frames"], 0)
                if os.environ.get("NETVUE_TEST_HA"):
                    self.assertGreater(readers["home_assistant_ffprobe"]["packets"], 0)
                    self.assertGreater(readers["home_assistant_ffmpeg"]["decoded_frames"], 0)
                return
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"decoded_frames":', result.stdout)
            if reconfigure:
                line = next(line for line in result.stdout.splitlines() if line.startswith("bridge: "))
                lines = json.loads(line.split(": ", 1)[1])
                metrics_line = next(line for line in lines if line.startswith("live_metrics: "))
                metrics = json.loads(metrics_line.split(": ", 1)[1])
                self.assertGreater(metrics["sps_changes"], 0)
                if pps_change:
                    self.assertGreater(metrics["pps_changes"], 0)
                if resize:
                    self.assertGreater(metrics["resolution_changes"], 0)
                if reset_pts:
                    self.assertEqual(metrics["timestamp_resets"], 1)
            if drop_frame:
                line = next(line for line in result.stdout.splitlines() if line.startswith("bridge: "))
                lines = json.loads(line.split(": ", 1)[1])
                metrics_line = next(line for line in lines if line.startswith("live_metrics: "))
                metrics = json.loads(metrics_line.split(": ", 1)[1])
                self.assertEqual(metrics["video_packets"], (duration - 1) * 10 - 1)
            if reconfigure and resize:
                self.assertIn("decoded_dimensions: [[64, 64], [128, 64]]", result.stdout)
            if os.environ.get("NETVUE_TEST_HA"):
                self.assertIn("home_assistant_rtsp:", result.stdout)
            self.assertEqual(counts, ["login"])
            print("synthetic bounded check:", result.stdout.strip())
        finally:
            udp.close()
            tcp.close()
            thread.join(12)
            self.assertFalse(thread.is_alive())
            self.assertFalse(failures, str(failures))


if __name__ == "__main__":
    unittest.main()
