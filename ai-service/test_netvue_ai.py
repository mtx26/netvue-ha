import json
import unittest
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from unittest.mock import MagicMock, patch

import av
import numpy as np
from onnxruntime.capi.onnxruntime_pybind11_state import Fail

from netvue_ai import (
    AiService,
    CameraWorker,
    Detection,
    IoUTracker,
    Track,
    YoloDetector,
    EventStore,
    iou,
    Zone,
    parse_zones,
    video_frames,
)


class AiUnitTest(unittest.TestCase):
    def test_overlapping_zones_confirm_independent_tracks_and_allow_reentry(self):
        zones = {
            "allee": {"name": "Allée", "points": [[0, 0], [0.8, 0], [0.8, 0.8]]},
            "portail": [0, 0, 0.9, 0.9],
        }
        worker = CameraWorker("test", parse_zones(zones), MagicMock(confidence=0.45), 3, 1)
        first = Track(1, "car", (20, 5, 30, 15))
        second = Track(2, "car", (40, 10, 50, 20))
        for timestamp in (0, 0.5):
            self.assertEqual(worker.update_zones([first, second], [first], timestamp, (100, 100))[1], [])
        memberships, entries = worker.update_zones([first, second], [first, second], 1, (100, 100))
        self.assertEqual(memberships, {1: ["allee", "portail"], 2: []})
        self.assertEqual(entries, [(first, "allee"), (first, "portail")])
        worker.update_zones([first, second], [first, second], 1.5, (100, 100))
        memberships, entries = worker.update_zones([first, second], [first, second], 2, (100, 100))
        self.assertEqual(entries, [(second, "allee"), (second, "portail")])
        with patch("netvue_ai.time.time", return_value=2):
            for track, zone in [(first, "allee"), (first, "portail"), *entries]:
                worker.emit("zone_entered", track.label, 0.9, track.track_id,
                            zone, zone_ids=memberships[track.track_id])
        self.assertEqual(len({event["id"] for event in worker.events}), 4)

        first.box = (90, 90, 100, 100)
        worker.update_zones([first, second], [first, second], 3, (100, 100))
        memberships, entries = worker.update_zones([first, second], [first, second], 4, (100, 100))
        self.assertEqual(memberships, {1: [], 2: ["allee", "portail"]})
        self.assertEqual(entries, [])
        first.box = (20, 5, 30, 15)
        for timestamp in (4.5, 5):
            worker.update_zones([first, second], [first, second], timestamp, (100, 100))
        _, entries = worker.update_zones([first, second], [first, second], 5.5, (100, 100))
        self.assertEqual(entries, [(first, "allee"), (first, "portail")])

    def test_entry_is_emitted_once_and_reentry_has_a_new_presence(self):
        worker = CameraWorker("test", parse_zones({"gate": [0, 0, 1, 1]}), MagicMock(), 3, 1)
        car = Track(1, "car", (20, 5, 30, 15))

        def update(timestamp, observed=True):
            return worker.update_zones([car], [car] if observed else [], timestamp, (100, 100))[1]

        self.assertEqual(update(0), [])
        self.assertEqual(update(0.5), [])
        self.assertEqual(update(1), [(car, "gate")])
        first_presence = car.active_zones["gate"].presence_id
        self.assertEqual(update(2), [])
        self.assertEqual(update(3, observed=False), [])
        self.assertEqual(update(3.5), [])
        self.assertEqual(car.active_zones["gate"].presence_id, first_presence)
        self.assertEqual(update(5.5, observed=False), [])
        self.assertEqual(update(6), [])
        self.assertEqual(update(6.5), [])
        self.assertEqual(update(7), [(car, "gate")])
        self.assertNotEqual(car.active_zones["gate"].presence_id, first_presence)

    def test_event_store_persists_lifecycle_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.json"
            store = EventStore(str(path))
            event = {
                "id": "event-1", "timestamp": 1.0, "stream_name": "camera",
                "event": "event_confirmed", "category": "person",
                "track_id": 1, "zone": "entrance",
            }
            self.assertTrue(store.add(event))
            self.assertFalse(store.add(event))
            recovered = EventStore(str(path))
            self.assertEqual([item["id"] for item in recovered.events], ["event-1"])

    def test_rule_schedule_crosses_midnight(self):
        worker = CameraWorker("test", {}, MagicMock(confidence=0.45), fps=1, confirmation=2)
        rule = {"days": [0, 1, 2, 3, 4, 5, 6], "start": "22:00", "end": "06:00"}
        midnight = time.mktime(time.strptime("2026-01-02 23:30", "%Y-%m-%d %H:%M"))
        dawn = time.mktime(time.strptime("2026-01-02 05:30", "%Y-%m-%d %H:%M"))
        noon = time.mktime(time.strptime("2026-01-02 12:00", "%Y-%m-%d %H:%M"))
        self.assertTrue(worker._rule_matches_time(rule, midnight))
        self.assertTrue(worker._rule_matches_time(rule, dawn))
        self.assertFalse(worker._rule_matches_time(rule, noon))
    def test_two_cars_confirm_and_survive_independent_short_misses(self):
        tracker = IoUTracker(confirmation_frames=2)
        left = Detection(2, "car", 0.65, (0, 0, 30, 30))
        right = Detection(2, "car", 0.52, (50, 0, 90, 40))
        tracker.update([left, right])
        self.assertEqual(tracker.visible(), [])
        tracker.update([left, right])
        self.assertEqual([track.box for track in tracker.visible()], [left.box, right.box])
        for _ in range(5):
            matched = tracker.update([right])
            self.assertEqual([track.box for track in matched], [right.box])
        self.assertEqual([track.box for track in tracker.visible()], [left.box, right.box])
        tracker.update([right])
        self.assertEqual([track.box for track in tracker.visible()], [right.box])

    def test_metadata_saves_one_sample_per_frame_and_clears_on_first_empty_update(self):
        worker = CameraWorker("netvue_192_0_2_1", {}, MagicMock(confidence=0.45), 3, 1)
        track = Track(track_id=1, label="car", confidence=0.9, box=(5, 5, 40, 30))
        other = Track(track_id=2, label="person", confidence=0.8, box=(7, 7, 20, 30))
        with patch("netvue_ai.urllib.request.urlopen") as post:
            post.return_value.__enter__.return_value.status = 201
            worker.persist_metadata(1.0, (48, 64), [])
            worker.persist_metadata(2.0, (48, 64), [track, other])
            worker.persist_metadata(2.5, (48, 64), [])
            worker.persist_metadata(3.0, (48, 64), [])
        seen = [json.loads(call.args[0].data) for call in post.call_args_list]
        self.assertEqual([sample["timestamp"] for sample in seen], [2.0, 2.5])
        self.assertEqual([box["category"] for box in seen[0]["boxes"]], ["car", "person"])
        self.assertEqual((seen[1]["event"], seen[1]["boxes"]), ("object_cleared", []))

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
        zone = Zone.from_config("gate", [0.0, 0.5, 0.5, 1.0])
        self.assertIsNotNone(zone)
        self.assertEqual(Zone.from_config("gate", zone.to_config()), zone)
        self.assertTrue(zone.contains((100, 300, 200, 500), (500, 1000)))
        self.assertFalse(zone.contains((600, 300, 700, 500), (500, 1000)))

    def test_saved_zones_round_trip_through_json(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"NETVUE_AI_CONFIG_DIR": directory}),
            patch("netvue_ai.YoloDetector"),
        ):
            config_path = Path(directory) / "netvue-ai-config.json"
            config_path.write_text('{"camera": {"zones": {"gate": [0, 0, 1, 1]}}}', encoding="utf-8")
            service = AiService()
            zone = service.configs["camera"]["zones"]["gate"]
            service.save_configs()

            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["camera"]["zones"]["gate"], {
                "name": "gate", "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
            })
            self.assertEqual(AiService().configs["camera"]["zones"]["gate"], zone)

    def test_renaming_preserves_presence_and_redrawing_clears_it(self):
        worker = CameraWorker("test", parse_zones({"gate": [0, 0, 1, 1]}), MagicMock(), 3, 1)
        car = Track(1, "car", (20, 5, 30, 15))
        for when in (1, 2, 3):
            worker.update_zones([car], [car], when, (100, 100))
        config = {"enabled": True, "confidence": 0.4, "fps": 3, "confirmation_frames": 1}
        worker.apply_config({**config, "zones": parse_zones({
            "gate": {"name": "New gate", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        })})
        memberships, events = worker.update_zones([car], [car], 4, (100, 100))
        self.assertEqual(memberships, {1: ["gate"]})
        self.assertEqual(events, [])
        worker.apply_config({**config, "zones": parse_zones({
            "gate": {"name": "New gate", "points": [[0.5, 0.5], [1, 0.5], [1, 1], [0.5, 1]]},
        })})
        memberships, events = worker.update_zones([car], [car], 5, (100, 100))
        self.assertEqual(memberships, {1: []})
        self.assertEqual(events, [])

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
        worker = CameraWorker("test", {}, detector, fps=1, confirmation=1)
        results = [[Detection(2, "car", 0.8, (5, 5, 30, 30))], Fail("ONNX failed"), []]
        snapshots = []

        def frames():
            for _ in range(4):
                yield av.VideoFrame(64, 48)
                snapshots.append(worker.snapshot())

        def detect(_frame, _confidence):
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        detector.detect.side_effect = detect
        with (
            patch("netvue_ai.video_frames", side_effect=lambda _url: frames()),
            patch("netvue_ai.time.monotonic", side_effect=[1.0, 1.1, 2.0, 3.0]),
            patch("netvue_ai.time.sleep", side_effect=lambda _: setattr(worker, "running", False)),
            patch.object(worker, "persist_metadata") as persist,
            self.assertLogs("netvue_ai", level="WARNING") as logs,
        ):
            worker.run()

        metrics = worker.snapshot()
        self.assertEqual(metrics["frames_decoded"], 4)
        self.assertEqual(metrics["frames_skipped"], 1)
        self.assertEqual(metrics["inference_count"], 3)
        self.assertEqual(metrics["inference_errors"], 1)
        self.assertEqual(metrics["error_count"], 1)
        self.assertEqual(metrics["inference_fps"], 1.0)
        self.assertIn("Inference failed for test", logs.output[0])
        self.assertEqual(snapshots[0]["detections"][0]["category"], "car")
        self.assertEqual(snapshots[2]["state"], "inference_error")
        self.assertEqual(snapshots[2]["detections"], [])
        self.assertEqual(snapshots[3]["state"], "detecting")
        self.assertEqual(snapshots[3]["detections"], [])
        self.assertFalse(persist.call_args_list[1].args[2])

    def test_worker_ignores_duplicate_pts_and_pixels_but_accepts_static_new_frames(self):
        detector = MagicMock(confidence=0.45)
        detector.detect.return_value = [Detection(0, "person", 0.9, (10, 10, 20, 20))]
        worker = CameraWorker("test", {}, detector, fps=1, confirmation=1)
        snapshots = []

        def frames():
            for pts in (1, 1, 2):
                frame = av.VideoFrame.from_ndarray(
                    np.zeros((48, 64, 3), dtype=np.uint8), format="bgr24"
                )
                frame.pts = pts
                frame.time_base = Fraction(1, 1000)
                yield frame
                snapshots.append(worker.snapshot())
            worker.running = False

        with (
            patch("netvue_ai.video_frames", side_effect=lambda _url: frames()),
            patch("netvue_ai.time.monotonic", side_effect=[1.0, 2.0, 3.0]),
            patch("netvue_ai.time.sleep"),
            patch.object(worker, "persist_metadata"),
        ):
            worker.run()
        self.assertEqual(detector.detect.call_count, 2)
        self.assertEqual(snapshots[0]["last_frame_at"], snapshots[1]["last_frame_at"])
        self.assertEqual(snapshots[2]["frames_skipped"], 1)
        self.assertEqual(snapshots[2]["inference_count"], 2)

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
