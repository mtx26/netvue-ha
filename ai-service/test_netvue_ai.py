import unittest
import tempfile
from fractions import Fraction
from pathlib import Path
from unittest.mock import MagicMock, patch

import av
import numpy as np

from netvue_ai import (
    AiService,
    CameraWorker,
    Detection,
    IoUTracker,
    YoloDetector,
    iou,
    point_in_zone,
    video_frames,
)


class AiUnitTest(unittest.TestCase):
    def test_enabled_logs_only_state_changes(self):
        worker = CameraWorker("test", {}, MagicMock(confidence=0.45), fps=1, confirmation=2)
        with self.assertLogs("netvue_ai", level="INFO") as logs:
            worker.set_enabled(False)
            worker.set_enabled(False)
            worker.set_enabled(True)
        self.assertEqual(len(logs.output), 2)
        self.assertIn("AI disabled for test", logs.output[0])
        self.assertIn("AI enabled for test", logs.output[1])

    def test_detection_logs_respect_event_cooldown(self):
        worker = CameraWorker("test", {}, MagicMock(confidence=0.45), fps=1, confirmation=2)
        with self.assertLogs("netvue_ai", level="INFO") as logs:
            worker.emit("object_detected", "dog", 0.9, 1)
            worker.emit("object_detected", "dog", 0.9, 1)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("category=dog confidence=0.90", logs.output[0])
        self.assertEqual(len(worker.events), 1)

    def test_event_cooldown_suppresses_new_tracks_but_not_other_zones(self):
        worker = CameraWorker("test", {}, MagicMock(confidence=0.45), fps=1, confirmation=2)
        with (
            patch("netvue_ai.time.time", return_value=100),
            self.assertLogs("netvue_ai", level="INFO") as logs,
        ):
            worker.emit("zone_entered", "car", 0.9, 1, "entrance")
            worker.emit("zone_entered", "car", 0.9, 2, "entrance")
            worker.emit("zone_entered", "car", 0.9, 3, "garden")
        self.assertEqual(len(worker.events), 2)
        self.assertEqual(len(logs.output), 2)

    def test_idle_sampling_stays_periodic_and_speeds_up_after_a_detection(self):
        worker = CameraWorker("test", {}, MagicMock(confidence=0.45), fps=3, confirmation=2)
        quiet_interval = worker._active_interval

        worker._update_sampling_interval(10, False)
        self.assertEqual(worker._active_interval, quiet_interval)
        worker._update_sampling_interval(11, True)
        self.assertEqual(worker._active_interval, worker.interval)
        worker._update_sampling_interval(12.9, False)
        self.assertEqual(worker._active_interval, worker.interval)
        worker._update_sampling_interval(13.1, False)
        self.assertEqual(worker._active_interval, quiet_interval)

    def test_discovery_failure_is_logged(self):
        with (
            patch.dict("netvue_ai.os.environ", {"NETVUE_AI_STREAM_NAMES": ""}),
            patch("netvue_ai.urllib.request.urlopen", side_effect=OSError("offline")),
            self.assertLogs("netvue_ai", level="WARNING") as logs,
        ):
            names = AiService.discover_stream_names(MagicMock())
        self.assertEqual(names, [])
        self.assertIn("Cannot discover MediaMTX streams: offline", logs.output[0])

    def test_iou_tracks_same_object_across_frames(self):
        tracker = IoUTracker(confirmation_frames=3)
        first = Detection(0, "person", 0.9, (100, 100, 200, 300))
        second = Detection(0, "person", 0.9, (108, 100, 208, 300))
        tracker.update([first])
        tracker.update([second])
        confirmed = tracker.update([second])
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0].label, "person")

    def test_iou_rejects_different_object_position(self):
        self.assertLess(iou((0, 0, 10, 10), (100, 100, 110, 110)), 0.01)

    def test_yolo_uses_letterbox_coordinates_and_suppresses_duplicate_boxes(self):
        output = np.zeros((1, 84, 2), dtype=np.float32)
        output[0, :4, 0] = [320, 320, 100, 100]
        output[0, :4, 1] = [322, 320, 100, 100]
        output[0, 4 + 2, 0] = 0.9
        output[0, 4 + 2, 1] = 0.8
        detector = YoloDetector.__new__(YoloDetector)
        detector.input_name = "images"
        detector.input_size = 640
        detector.confidence = 0.45
        detector.session = MagicMock()
        detector.session.run.return_value = [output]

        detections = detector.detect(np.zeros((480, 1280, 3), dtype=np.uint8))

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].label, "car")
        self.assertEqual(detections[0].box, (540.0, 140.0, 740.0, 340.0))
        tensor = detector.session.run.call_args.args[1]["images"][0]
        self.assertTrue(np.all(tensor[:, :200, :] == 114 / 255))

    def test_zone_uses_normalized_coordinates(self):
        rect = [0.0, 0.5, 0.5, 1.0]
        self.assertTrue(point_in_zone((100, 300, 200, 500), rect, (500, 1000)))
        self.assertFalse(point_in_zone((600, 300, 700, 500), rect, (500, 1000)))

    def test_video_resolution_changes_preserve_dimensions_and_pixels(self):
        sizes = [(640, 360), (1920, 1080), (640, 360)]
        encoded = []
        for width, height in sizes:
            codec = av.CodecContext.create("libx264", "w")
            codec.width, codec.height = width, height
            codec.pix_fmt = "yuv420p"
            codec.time_base = Fraction(1, 5)
            for _ in range(3):
                frame = av.VideoFrame.from_ndarray(
                    np.full((height, width, 3), 80, dtype=np.uint8), format="bgr24"
                )
                encoded.extend(bytes(packet) for packet in codec.encode(frame))
            encoded.extend(bytes(packet) for packet in codec.encode(None))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "changing.h264"
            path.write_bytes(b"".join(encoded))
            frames = [frame.to_ndarray(format="bgr24") for frame in video_frames(str(path))]
        self.assertEqual(
            [frame.shape[:2] for frame in frames],
            [(height, width) for width, height in sizes for _ in range(3)],
        )
        for frame in frames:
            self.assertLess(abs(float(frame.mean()) - 80), 3)

    def test_video_reader_closes_container_when_stopped(self):
        container = MagicMock()
        container.__enter__.return_value = container
        container.decode.return_value = iter([av.VideoFrame(64, 48)])
        with patch("netvue_ai.av.open", return_value=container) as open_video:
            reader = video_frames("rtsp://127.0.0.1:8554/test")
            next(reader)
            reader.close()
        container.__exit__.assert_called_once()
        open_video.assert_called_once_with(
            "rtsp://127.0.0.1:8554/test",
            options={"rtsp_transport": "tcp"}, timeout=(5.0, 5.0),
        )

    def test_worker_counts_skipped_frames_and_recovers_from_inference_error(self):
        detector = MagicMock(confidence=0.45)
        worker = CameraWorker("test", {}, detector, fps=1, confirmation=2)
        results = [RuntimeError("inference failed"), []]

        def frames():
            for _ in range(3):
                yield av.VideoFrame(64, 48)

        def detect(_frame, _confidence):
            if detector.detect.call_count == 2:
                worker.running = False
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        detector.detect.side_effect = detect
        with (
            patch("netvue_ai.video_frames", side_effect=lambda _url: frames()),
            patch("netvue_ai.time.monotonic", side_effect=[1.0, 1.1, 2.0]),
            patch("netvue_ai.time.sleep"),
            self.assertLogs("netvue_ai", level="WARNING") as logs,
        ):
            worker.run()

        metrics = worker.snapshot()
        self.assertEqual(metrics["frames_decoded"], 3)
        self.assertEqual(metrics["frames_skipped"], 1)
        self.assertEqual(metrics["inference_count"], 2)
        self.assertEqual(metrics["inference_errors"], 1)
        self.assertEqual(metrics["error_count"], 1)
        self.assertEqual(metrics["inference_fps"], 1.0)
        self.assertIn("Inference failed for test", logs.output[0])

    def test_worker_resets_confirmation_after_resolution_change(self):
        detector = MagicMock(confidence=0.45)
        worker = CameraWorker("test", {}, detector, fps=1, confirmation=2)
        dimensions = [(48, 64), (96, 128), (96, 128)]
        closed = []

        def frames():
            try:
                for height, width in dimensions:
                    yield av.VideoFrame.from_ndarray(
                        np.zeros((height, width, 3), dtype=np.uint8), format="bgr24"
                    )
            finally:
                closed.append(True)

        def detect(_frame, _confidence):
            if detector.detect.call_count == 3:
                worker.running = False
            return [Detection(0, "person", 0.9, (10, 10, 20, 20))]

        detector.detect.side_effect = detect
        with (
            patch("netvue_ai.video_frames", side_effect=lambda _url: frames()),
            patch("netvue_ai.time.monotonic", side_effect=[1.0, 2.0, 3.0]),
            patch("netvue_ai.time.sleep"),
            self.assertLogs("netvue_ai", level="INFO") as logs,
        ):
            worker.run()
        self.assertEqual([args[0].shape[:2] for args, _ in detector.detect.call_args_list],
                         dimensions)
        self.assertEqual(len(worker.events), 1)
        metrics = worker.snapshot()
        self.assertEqual(metrics["frames_decoded"], 3)
        self.assertEqual(metrics["frames_skipped"], 0)
        self.assertEqual(metrics["inference_count"], 3)
        self.assertEqual(metrics["positive_inference_count"], 1)
        self.assertGreater(metrics["inference_latency_ms"], 0)
        self.assertEqual(closed, [True])
        self.assertTrue(any("Video ready for test: 128x96" in line for line in logs.output))
        self.assertTrue(any("category=person" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
