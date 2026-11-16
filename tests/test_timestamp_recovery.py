"""Focused H.264 timestamp recovery tests using synthetic encoded pictures."""

import unittest
from collections import deque
from fractions import Fraction
from unittest.mock import MagicMock, patch

try:
    import av
except ModuleNotFoundError:
    av = None

from netvue_bridge.protocol import ProtocolError, RecoverableVideoError
from netvue_bridge.h264 import TimestampClock, interpolate_duplicate_timestamps
from netvue_bridge.rtsp import H264Publisher, annexb_nals


class TimestampInterpolationTests(unittest.TestCase):
    def test_clock_duplicate_is_a_recoverable_session_error(self):
        clock = TimestampClock()
        clock.update(100)
        with self.assertRaisesRegex(RecoverableVideoError, "Duplicate video timestamp"):
            clock.update(100)

    def test_interpolation_preserves_cadence_across_wrap(self):
        intervals = deque([100, 100, 100])
        self.assertEqual(interpolate_duplicate_timestamps(300, 600, 2, intervals), [400, 500])
        self.assertEqual(interpolate_duplicate_timestamps(0xFFFFFF9B, 99, 1, intervals),
                         [0xFFFFFFFF])

    def test_interpolation_rejects_missing_history_and_unsafe_gap(self):
        with self.assertRaisesRegex(ProtocolError, "cadence"):
            interpolate_duplicate_timestamps(None, 100, 1, deque([100] * 3))
        with self.assertRaisesRegex(ProtocolError, "safely interpolated"):
            interpolate_duplicate_timestamps(300, 301, 1, deque([100] * 3))


@unittest.skipIf(av is None, "requires PyAV and libx264")
class TimestampRecoveryTests(unittest.TestCase):
    def setUp(self):
        codec = av.CodecContext.create("libx264", "w")
        codec.width = codec.height = 64
        codec.pix_fmt = "yuv420p"
        codec.time_base = Fraction(1, 10)
        codec.options = {"preset": "ultrafast", "tune": "zerolatency",
                         "x264-params": "keyint=100:bframes=0:slices=2:scenecut=0"}
        codec.open()
        self.pictures = []
        for index in range(16):
            frame = av.VideoFrame(64, 64, "yuv420p")
            frame.pts = index
            for plane in frame.planes:
                plane.update(bytes([index]) * plane.buffer_size)
            self.pictures.extend(bytes(packet) for packet in codec.encode(frame))
        units = annexb_nals(self.pictures[0])
        self.publisher = H264Publisher("rtsp://127.0.0.1:8554/test")
        self.publisher.sps = next(nal for nal in units if nal[0] & 31 == 7)
        self.publisher.pps = next(nal for nal in units if nal[0] & 31 == 8)
        self.publisher.started = True
        self.publisher.output = MagicMock()
        self.mux = patch.object(self.publisher, "_mux")
        self.mux_mock = self.mux.start()
        self.addCleanup(self.mux.stop)
        self.addCleanup(self.publisher.close)

    def warm_up(self):
        for index in range(4):
            self.publisher.push(self.pictures[index], index * 100)

    def test_interpolates_distinct_multi_slice_pictures_without_drift(self):
        self.warm_up()
        self.assertGreaterEqual(sum((n[0] & 31) in (1, 5)
                                    for n in annexb_nals(self.pictures[4])), 2)
        self.publisher.push(self.pictures[4], 300)
        self.publisher.push(self.pictures[5], 300)
        self.assertEqual(self.mux_mock.call_count, 4)
        self.publisher.push(self.pictures[6], 600)
        pts = [call.args[1] for call in self.mux_mock.call_args_list]
        self.assertEqual(pts, [0, 100, 200, 300, 400, 500, 600])
        metrics = self.publisher.safe_metrics()
        self.assertEqual(metrics["same_timestamps"], 2)
        self.assertEqual(metrics["timestamp_corrections"], 2)
        self.assertEqual(metrics["max_frame_gap_ms"], 100)
        self.assertEqual(metrics["last_valid_timestamp"], 600)

    def test_identical_payloads_and_fragmented_picture_fail_closed(self):
        self.warm_up()
        with self.assertRaisesRegex(ProtocolError, "ambiguous"):
            self.publisher.push(self.pictures[3], 300)
        self.assertEqual(self.mux_mock.call_count, 4)
        units = annexb_nals(self.pictures[4])
        slices = [nal for nal in units if nal[0] & 31 in (1, 5)]
        fragment = b"\x00\x00\x00\x01" + slices[-1]
        with self.assertRaisesRegex(ProtocolError, "boundary"):
            self.publisher.push(fragment, 300)

    def test_missing_cadence_and_discontinuity_are_not_guessed(self):
        for index in range(2):
            self.publisher.push(self.pictures[index], index * 100)
        self.publisher.push(self.pictures[2], 100)
        with self.assertRaisesRegex(ProtocolError, "cadence"):
            self.publisher.push(self.pictures[3], 300)

    def test_invalid_anchor_fails_closed(self):
        self.warm_up()
        self.publisher.push(self.pictures[4], 300)
        with self.assertRaisesRegex(ProtocolError, "safely interpolated"):
            self.publisher.push(self.pictures[5], 301)

    def test_collision_timeout_fails_closed(self):
        self.warm_up()
        self.publisher.push(self.pictures[4], 300)
        self.publisher._collision_started -= 3
        with self.assertRaisesRegex(ProtocolError, "timed out"):
            self.publisher.push(self.pictures[5], 500)

    def test_collision_buffer_is_bounded(self):
        self.warm_up()
        for index in range(4, 12):
            self.publisher.push(self.pictures[index], 300)
        with self.assertRaisesRegex(ProtocolError, "buffer limit"):
            self.publisher.push(self.pictures[12], 300)

    def test_overlap_with_live_confirmation_fails_closed(self):
        self.warm_up()
        self.publisher.note_repeated_live_confirmation()
        with self.assertRaisesRegex(ProtocolError, "LIVE confirmation"):
            self.publisher.push(self.pictures[4], 300)


if __name__ == "__main__":
    unittest.main()
