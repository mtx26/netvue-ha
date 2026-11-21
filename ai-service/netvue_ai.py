"""Local RTSP object detection service for Netvue cameras."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable
from collections.abc import Iterator

import av
import cv2
import numpy as np
import onnxruntime as ort
from aiohttp import web
from onnxruntime.capi.onnxruntime_pybind11_state import Fail, InvalidArgument, RuntimeException

COCO_NAMES = {
    0: "person", 14: "bird", 15: "cat", 16: "dog",
    2: "car", 5: "bus", 7: "truck", 1: "bicycle", 3: "motorcycle",
}
TRACKED_CLASSES = set(COCO_NAMES)
ZONE_NAMES = {"entrance", "doorstep", "garden", "driveway"}
_LOGGER = logging.getLogger(__name__)
NMS_IOU_THRESHOLD = 0.45
EVENT_COOLDOWN_SECONDS = 30
QUIET_INFERENCE_INTERVAL = 1.0
DETECTION_GRACE_PERIOD = 2.0
PRE_EVENT_SECONDS = 120
POST_EVENT_SECONDS = 300


def video_frames(url: str) -> Iterator[av.VideoFrame]:
    with av.open(url, options={"rtsp_transport": "tcp"}, timeout=(5.0, 5.0)) as container:
        yield from container.decode(video=0)


@dataclass
class Detection:
    class_id: int
    label: str
    confidence: float
    box: tuple[float, float, float, float]


@dataclass
class Track:
    track_id: int
    label: str
    box: tuple[float, float, float, float]
    confidence: float = 0.0
    confirmed_frames: int = 1
    zone_hits: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.zone_hits is None:
            self.zone_hits = {}


class EventStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self.events: list[dict] = []
        self._lock = threading.Lock()
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                self.events = [item for item in data[-1000:] if isinstance(item, dict)]
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self.events = []

    def save(self) -> None:
        temporary = f"{self.path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.events[-1000:], handle, ensure_ascii=False, sort_keys=True)
        os.replace(temporary, self.path)

    def add(self, event: dict) -> bool:
        event_id = event.get("id")
        with self._lock:
            if any(item.get("id") == event_id for item in self.events):
                return False
            self.events.append(event)
            del self.events[:-1000]
            self.save()
        return True


def iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = inter_w * inter_h
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union else 0.0


class IoUTracker:
    def __init__(self, confirmation_frames: int = 3) -> None:
        self.confirmation_frames = confirmation_frames
        self.tracks: dict[int, Track] = {}
        self.next_id = 1
        self.last_seen: dict[int, int] = {}
        self.age = 0

    def update(self, detections: Iterable[Detection]) -> list[Track]:
        self.age += 1
        unmatched_tracks = set(self.tracks)
        matched: list[Track] = []
        for detection in sorted(detections, key=lambda candidate: -candidate.confidence):
            best_id = 0
            best_iou = 0.20
            for track_id in tuple(unmatched_tracks):
                track = self.tracks[track_id]
                if track.label != detection.label:
                    continue
                score = iou(track.box, detection.box)
                if score > best_iou:
                    best_iou = score
                    best_id = track_id
            if best_id:
                track = self.tracks[best_id]
                track.box = detection.box
                track.confidence = max(track.confidence, detection.confidence)
                track.confirmed_frames += 1
                unmatched_tracks.discard(best_id)
            else:
                track = Track(
                    self.next_id, detection.label, detection.box,
                    confidence=detection.confidence,
                )
                self.next_id += 1
                self.tracks[track.track_id] = track
            self.last_seen[track.track_id] = self.age
            if track.confirmed_frames >= self.confirmation_frames:
                matched.append(track)
        for track_id in unmatched_tracks:
            if self.age - self.last_seen[track_id] > 5:
                del self.tracks[track_id], self.last_seen[track_id]
        return matched

    def visible(self) -> list[Track]:
        return [
            track for track in self.tracks.values()
            if track.confirmed_frames >= self.confirmation_frames
        ]


class YoloDetector:
    def __init__(self, model_path: str, confidence: float) -> None:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            model_path, sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        self.input_size = int(self.session.get_inputs()[0].shape[2])
        self.confidence = confidence

    def detect(self, frame: np.ndarray, confidence: float | None = None) -> list[Detection]:
        height, width = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        scale = min(self.input_size / width, self.input_size / height)
        resized_width = max(1, int(round(width * scale)))
        resized_height = max(1, int(round(height * scale)))
        pad_x = (self.input_size - resized_width) // 2
        pad_y = (self.input_size - resized_height) // 2
        resized = cv2.resize(rgb, (resized_width, resized_height))
        letterboxed = np.full(
            (self.input_size, self.input_size, 3), 114, dtype=np.uint8
        )
        letterboxed[
            pad_y:pad_y + resized_height, pad_x:pad_x + resized_width
        ] = resized
        tensor = letterboxed.transpose(2, 0, 1)[np.newaxis].astype(np.float32) / 255.0
        output = self.session.run(None, {self.input_name: tensor})[0]
        predictions = np.asarray(output).squeeze()
        if predictions.ndim == 1:
            predictions = predictions[np.newaxis, :]
        if predictions.shape[0] == 84:
            predictions = predictions.T

        if predictions.ndim != 2 or predictions.shape[1] < 6:
            return []
        class_scores = predictions[:, 4:]
        class_ids = class_scores.argmax(axis=1)
        scores = class_scores[np.arange(len(class_scores)), class_ids]
        threshold = self.confidence if confidence is None else confidence
        selected = (
            np.isin(class_ids, tuple(TRACKED_CLASSES))
            & (scores >= threshold)
            & np.isfinite(scores)
            & np.isfinite(predictions[:, :4]).all(axis=1)
        )
        if not selected.any():
            return []

        selected_class_ids = class_ids[selected]
        selected_scores = scores[selected]
        centers = predictions[selected, :2]
        sizes = predictions[selected, 2:4]
        x1 = np.clip((centers[:, 0] - sizes[:, 0] / 2 - pad_x) / scale, 0, width)
        y1 = np.clip((centers[:, 1] - sizes[:, 1] / 2 - pad_y) / scale, 0, height)
        x2 = np.clip((centers[:, 0] + sizes[:, 0] / 2 - pad_x) / scale, 0, width)
        y2 = np.clip((centers[:, 1] + sizes[:, 1] / 2 - pad_y) / scale, 0, height)
        valid = (x2 > x1) & (y2 > y1)
        detections: list[Detection] = []
        for class_id in np.unique(selected_class_ids[valid]):
            class_indices = np.flatnonzero((selected_class_ids == class_id) & valid)
            nms_boxes = [
                [float(x1[index]), float(y1[index]),
                 float(x2[index] - x1[index]), float(y2[index] - y1[index])]
                for index in class_indices
            ]
            keep = cv2.dnn.NMSBoxes(
                nms_boxes, selected_scores[class_indices].tolist(),
                threshold, NMS_IOU_THRESHOLD,
            )
            for index in np.asarray(keep).reshape(-1):
                candidate = class_indices[int(index)]
                detections.append(Detection(
                    int(class_id),
                    COCO_NAMES[int(class_id)],
                    float(selected_scores[candidate]),
                    (
                        float(x1[candidate]), float(y1[candidate]),
                        float(x2[candidate]), float(y2[candidate]),
                    ),
                ))
        return detections


def load_zones() -> dict[str, dict[str, list[float]]]:
    raw = os.environ.get("NETVUE_AI_ZONES", "{}")
    try:
        zones = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(zones, dict):
        return {}
    return {
        stream: {
            name: value for name, value in config.items()
            if name in ZONE_NAMES and isinstance(value, list) and len(value) == 4
        }
        for stream, config in zones.items()
        if isinstance(config, dict)
    }


def point_in_zone(box: tuple[float, float, float, float], rect: list[float],
                  frame_size: tuple[int, int]) -> bool:
    height, width = frame_size
    cx = (box[0] + box[2]) / 2 / width
    cy = (box[1] + box[3]) / 2 / height
    x1, y1, x2, y2 = rect
    return x1 <= cx <= x2 and y1 <= cy <= y2


class CameraWorker:
    def __init__(self, stream_name: str, zones: dict[str, list[float]],
                 detector: YoloDetector, fps: float, confirmation: int) -> None:
        self.stream_name = stream_name
        self.zones = zones
        self.detector = detector
        self.interval = 1.0 / max(1.0, fps)
        self._active_interval = max(self.interval, QUIET_INFERENCE_INTERVAL)
        self._last_detection_at: float | None = None
        self._metadata_has_boxes = False
        self.tracker = IoUTracker(confirmation)
        self.events: list[dict] = []
        self.event_cursor = 0
        self.running = True
        self.thread = threading.Thread(target=self.run, name=f"ai-{stream_name}", daemon=True)
        self.status = {
            "stream_name": stream_name,
            "enabled": True,
            "state": "starting",
            "detections": [],
            "last_frame_at": None,
            "last_event_at": None,
            "package_states": [],
        }
        self.confidence = detector.confidence
        self._cooldowns: dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_inference_started_at: float | None = None
        self.rules: list[dict] = []
        self.event_store: EventStore | None = None
        self.active_lifecycle: dict[tuple[str, int], dict] = {}
        self.package_states: dict[int, dict] = {}
        self.status.update({
            "inference_latency_ms": None,
            "inference_fps": 0.0,
            "detection_rate": 0.0,
            "frames_decoded": 0,
            "frames_skipped": 0,
            "inference_count": 0,
            "positive_inference_count": 0,
            "error_count": 0,
            "inference_errors": 0,
            "last_error": None,
        })

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            changed = self.status["enabled"] != enabled
            self.status["enabled"] = enabled
        if changed:
            _LOGGER.info("AI %s for %s", "enabled" if enabled else "disabled", self.stream_name)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.status)

    def _record_inference(self, started_at: float, latency_ms: float,
                          has_detections: bool, error: str | None = None) -> None:
        with self._lock:
            self.status["inference_count"] += 1
            if has_detections:
                self.status["positive_inference_count"] = (
                    self.status.get("positive_inference_count", 0) + 1
                )
            count = self.status["inference_count"]
            positive_count = self.status.get("positive_inference_count", 0)
            self.status["inference_latency_ms"] = round(latency_ms, 2)
            if self._last_inference_started_at is not None:
                interval = started_at - self._last_inference_started_at
                if interval > 0:
                    self.status["inference_fps"] = round(1 / interval, 3)
            self._last_inference_started_at = started_at
            if error is not None:
                self.status["state"] = "inference_error"
                self.status["detections"] = []
                self.status["error_count"] += 1
                self.status["inference_errors"] += 1
                self.status["last_error"] = error[:200]
            else:
                self.status["last_error"] = None
            successful_count = count - self.status["inference_errors"]
            self.status["detection_rate"] = round(
                positive_count / successful_count, 3
            ) if successful_count else 0.0

    def _record_error(self, error: Exception) -> None:
        with self._lock:
            self.status["error_count"] += 1
            self.status["last_error"] = str(error)[:200]

    def _update_sampling_interval(self, now: float, has_detections: bool) -> None:
        if has_detections:
            self._last_detection_at = now
        active = (
            self._last_detection_at is not None
            and now - self._last_detection_at <= DETECTION_GRACE_PERIOD
        )
        self._active_interval = (
            self.interval if active else max(self.interval, QUIET_INFERENCE_INTERVAL)
        )

    @staticmethod
    def _rule_matches_time(rule: dict, timestamp: float) -> bool:
        local = time.localtime(timestamp)
        weekdays = rule.get("days", [])
        if weekdays and str(local.tm_wday) not in [str(day) for day in weekdays]:
            return False
        start = str(rule.get("start", "00:00"))
        end = str(rule.get("end", "23:59"))
        now_clock = f"{local.tm_hour:02d}:{local.tm_min:02d}"
        if start <= end:
            return start <= now_clock <= end
        return now_clock >= start or now_clock <= end

    def _matching_rules(self, track: Track, zone: str | None, timestamp: float) -> list[dict]:
        matches = []
        for rule in self.rules:
            if not rule.get("enabled", True):
                continue
            if rule.get("category") not in ("", "*", track.label):
                continue
            if rule.get("zone") and rule.get("zone") != zone:
                continue
            confidence = rule.get("confidence")
            if type(confidence) in (int, float) and track.confidence < float(confidence):
                continue
            if self._rule_matches_time(rule, timestamp):
                matches.append(rule)
        return matches

    def _persist_lifecycle_event(self, event_type: str, track: Track,
                                 zone: str | None, timestamp: float,
                                 rule: dict | None = None) -> dict:
        event = {
            "id": f"{int(timestamp * 1000)}-{self.stream_name}-{track.track_id}-{event_type}",
            "timestamp": timestamp,
            "stream_name": self.stream_name,
            "event": event_type,
            "category": track.label,
            "confidence": round(track.confidence, 3),
            "track_id": track.track_id,
            "zone": zone,
            "state": event_type,
            "rule": rule.get("name") if isinstance(rule, dict) else None,
            "protected_recording_id": None,
            "notification_status": "pending",
        }
        if self.event_store is not None:
            self.event_store.add(event)
        self.events.append(event)
        del self.events[:-1000]
        return event

    def _protect_lifecycle_range(self, start: float, end: float,
                                 track: Track, zone: str | None,
                                 rule: dict | None = None) -> None:
        protected_start = start - PRE_EVENT_SECONDS
        protected_end = end + POST_EVENT_SECONDS
        host = self.stream_name
        if host.startswith("netvue_"):
            host = host[len("netvue_"):].replace("_", ".")
        try:
            payload = json.dumps({
                "start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(protected_start)),
                "end": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(protected_end)),
                "title": f"{track.label} - {zone or 'whole image'}",
                "category": track.label,
                "protection": "automatic",
            }).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:8765/api/v1/cameras/{host}/protected",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
            recording_id = data.get("id") if isinstance(data, dict) else None
            if isinstance(recording_id, str):
                _LOGGER.info("Protected automatic recording %s for %s", recording_id, self.stream_name)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            _LOGGER.warning("Cannot protect AI event for %s: %s", self.stream_name, exc)

    def update_package_state(self, visible: Iterable[Track],
                             timestamp: float, frame_size: tuple[int, int]) -> None:
        packages = [track for track in visible if track.label in ("package", "parcel", "box")]
        seen: set[int] = set()
        for track in packages:
            seen.add(track.track_id)
            zone = next((
                name for name, rect in self.zones.items()
                if point_in_zone(track.box, rect, frame_size)
            ), None)
            state = self.package_states.get(track.track_id)
            if state is None:
                state = {
                    "track_id": track.track_id, "state": "candidate",
                    "first_seen": timestamp, "last_seen": timestamp,
                    "zone": zone, "confidence": track.confidence,
                }
                self.package_states[track.track_id] = state
                continue
            state["last_seen"] = timestamp
            state["confidence"] = max(float(state["confidence"]), track.confidence)
            state["zone"] = zone
            if state["state"] == "candidate" and timestamp - state["first_seen"] >= 3:
                state["state"] = "present"
                event = self._persist_lifecycle_event(
                    "package_deposited", track, zone, timestamp
                )
                event["package_state"] = "deposited"
        for track_id, state in list(self.package_states.items()):
            if track_id in seen:
                continue
            if state["state"] in ("present", "candidate") and timestamp - state["last_seen"] >= 5:
                state["state"] = "disappeared"
                track = Track(
                    track_id=track_id, label="package",
                    box=(0, 0, 0, 0), confidence=float(state["confidence"]),
                )
                event = self._persist_lifecycle_event(
                    "package_disappeared", track, state["zone"], timestamp
                )
                event["package_state"] = "disappeared"
                del self.package_states[track_id]
        with self._lock:
            self.status["package_states"] = list(self.package_states.values())

    def update_lifecycle(self, visible: Iterable[Track],
                         timestamp: float, frame_size: tuple[int, int]) -> None:
        height, width = frame_size
        seen: set[tuple[str, int]] = set()
        for track in visible:
            zone = next((
                name for name, rect in self.zones.items()
                if point_in_zone(track.box, rect, frame_size)
            ), None)
            key = (track.label, track.track_id)
            seen.add(key)
            active = self.active_lifecycle.get(key)
            if active is None:
                matches = self._matching_rules(track, zone, timestamp)
                if track.confirmed_frames < self.tracker.confirmation_frames:
                    continue
                active = {
                    "start": timestamp, "last_seen": timestamp, "zone": zone,
                    "track": track, "rule": matches[0] if matches else None,
                    "confirmed": bool(matches),
                }
                self.active_lifecycle[key] = active
                if active["confirmed"]:
                    self._persist_lifecycle_event("event_confirmed", track, zone, timestamp, active["rule"])
                    self._persist_lifecycle_event("event_active", track, zone, timestamp, active["rule"])
            else:
                active["last_seen"] = timestamp
                active["track"] = track
                if not active["confirmed"]:
                    matches = self._matching_rules(track, zone, timestamp)
                    if matches:
                        active["confirmed"] = True
                        active["rule"] = matches[0]
                        active["zone"] = zone
                        self._persist_lifecycle_event("event_confirmed", track, zone, timestamp, active["rule"])

        grace = max(DETECTION_GRACE_PERIOD, 3.0)
        for key, active in list(self.active_lifecycle.items()):
            if key in seen and timestamp - active["last_seen"] <= grace:
                continue
            if timestamp - active["last_seen"] <= grace:
                continue
            if active["confirmed"]:
                track = active["track"]
                zone = active["zone"]
                self._persist_lifecycle_event("event_ended", track, zone, timestamp, active["rule"])
                if active["rule"] is not None:
                    self._protect_lifecycle_range(active["start"], timestamp, track, zone, active["rule"])
            del self.active_lifecycle[key]

    def emit(self, event_type: str, label: str, confidence: float,
             track_id: int, zone: str | None = None) -> None:
        now = time.time()
        key = f"{event_type}:{label}:{zone or 'camera'}"
        if now - self._cooldowns.get(key, 0) < EVENT_COOLDOWN_SECONDS:
            return
        self._cooldowns[key] = now
        event = {
            "id": f"{int(now * 1000)}-{self.stream_name}-{track_id}",
            "timestamp": now,
            "stream_name": self.stream_name,
            "event": event_type,
            "category": label,
            "confidence": round(confidence, 3),
            "track_id": track_id,
            "zone": zone,
        }
        self.events.append(event)
        if len(self.events) > 200:
            del self.events[:-200]
        with self._lock:
            self.status["last_event_at"] = now
        _LOGGER.info(
            "Detection %s for %s: category=%s confidence=%.2f zone=%s",
            event_type, self.stream_name, label, confidence, zone or "whole_image",
        )

    def persist_metadata(self, timestamp: float, frame_size: tuple[int, int],
                         tracks: Iterable[Track]) -> None:
        tracks = list(tracks)[:32]
        if not tracks and not self._metadata_has_boxes:
            return
        host = self.stream_name
        if host.startswith("netvue_"):
            host = host[len("netvue_"):].replace("_", ".")
        boxes = [
            {
                "x1": round(track.box[0], 2),
                "y1": round(track.box[1], 2),
                "x2": round(track.box[2], 2),
                "y2": round(track.box[3], 2),
                "category": track.label,
                "confidence": round(track.confidence, 3),
            }
            for track in tracks
        ]
        track = tracks[0] if tracks else None
        event = {
            "id": f"{int(timestamp * 1000)}-{self.stream_name}-{track.track_id if track else 0}",
            "timestamp": timestamp,
            "stream_name": self.stream_name,
            "event": "object_detected" if track else "object_cleared",
            "category": track.label if track else "",
            "confidence": round(track.confidence, 3) if track else 0,
            "track_id": track.track_id if track else 0,
            "frame_width": frame_size[1],
            "frame_height": frame_size[0],
            "boxes": boxes,
        }
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:8765/api/v1/cameras/{host}/ai/metadata",
                data=json.dumps(event).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=1.0) as response:
                if response.status not in (200, 201):
                    raise OSError(f"bridge returned HTTP {response.status}")
            self._metadata_has_boxes = bool(boxes)
        except OSError as exc:
            _LOGGER.debug("Cannot persist AI metadata for %s: %s", self.stream_name, exc)

    def run(self) -> None:
        url = f"rtsp://127.0.0.1:8554/{self.stream_name}"
        while self.running:
            with self._lock:
                enabled = bool(self.status["enabled"])
            if not enabled:
                time.sleep(0.5)
                continue
            capture = video_frames(url)
            last_inference = 0.0
            frame_size = None
            try:
                while self.running:
                    with self._lock:
                        if not self.status["enabled"]:
                            break
                    try:
                        decoded = next(capture)
                    except StopIteration:
                        break
                    now = time.monotonic()
                    with self._lock:
                        self.status["frames_decoded"] += 1
                    if now - last_inference < self._active_interval:
                        with self._lock:
                            self.status["frames_skipped"] += 1
                        continue
                    last_inference = now
                    frame = decoded.to_ndarray(format="bgr24")
                    new_size = frame.shape[:2]
                    if new_size != frame_size:
                        _LOGGER.info("Video ready for %s: %dx%d", self.stream_name,
                                     new_size[1], new_size[0])
                        next_id = self.tracker.next_id
                        self.tracker = IoUTracker(self.tracker.confirmation_frames)
                        self.tracker.next_id = next_id
                        frame_size = new_size
                    inference_started = time.perf_counter()
                    try:
                        detections = self.detector.detect(frame, self.confidence)
                    except (RuntimeError, cv2.error, ValueError, Fail, InvalidArgument, RuntimeException) as exc:
                        self._update_sampling_interval(now, False)
                        self._record_inference(
                            now, (time.perf_counter() - inference_started) * 1000,
                            False, str(exc),
                        )
                        next_id = self.tracker.next_id
                        self.tracker = IoUTracker(self.tracker.confirmation_frames)
                        self.tracker.next_id = next_id
                        self.persist_metadata(time.time(), frame_size, ())
                        _LOGGER.warning("Inference failed for %s: %s", self.stream_name, exc)
                        continue
                    latency_ms = (time.perf_counter() - inference_started) * 1000
                    self._update_sampling_interval(now, bool(detections))
                    confirmed = self.tracker.update(detections)
                    visible = self.tracker.visible()
                    self._record_inference(now, latency_ms, bool(confirmed))
                    latest = [
                        {
                            "category": item.label,
                            "confidence": round(item.confidence, 3),
                            "track_id": item.track_id,
                            "box": {
                                "x1": round(item.box[0], 2),
                                "y1": round(item.box[1], 2),
                                "x2": round(item.box[2], 2),
                                "y2": round(item.box[3], 2),
                            },
                            "frame_width": frame_size[1],
                            "frame_height": frame_size[0],
                        }
                        for item in visible
                    ]
                    timestamp = time.time()
                    with self._lock:
                        self.status["state"] = "detecting"
                        self.status["detections"] = latest
                        self.status["last_frame_at"] = timestamp
                    self.persist_metadata(timestamp, frame_size, visible)
                    self.update_lifecycle(visible, timestamp, frame_size)
                    self.update_package_state(visible, timestamp, frame_size)
                    for track in confirmed:
                        zones_hit = [
                            name for name, rect in self.zones.items()
                            if point_in_zone(track.box, rect, frame_size)
                        ]
                        if zones_hit:
                            for zone in zones_hit:
                                before = track.zone_hits.get(zone, 0)
                                track.zone_hits[zone] = before + 1
                                if track.zone_hits[zone] == 3:
                                    self.emit("zone_entered", track.label, track.confidence,
                                              track.track_id, zone)
                        else:
                            self.emit("object_detected", track.label, track.confidence,
                                      track.track_id)
            except av.FFmpegError as exc:
                self._record_error(exc)
                _LOGGER.warning("Video decoding failed for %s: %s", self.stream_name, exc)
            finally:
                capture.close()
            with self._lock:
                self.status["state"] = "reconnecting"
                self.status["detections"] = []
            if self.running and self.status["enabled"]:
                _LOGGER.info("Reconnecting video for %s", self.stream_name)
            time.sleep(1)

    def start(self) -> None:
        _LOGGER.info("Starting AI worker for %s", self.stream_name)
        self.thread.start()

    def stop(self) -> None:
        _LOGGER.info("Stopping AI worker for %s", self.stream_name)
        self.running = False


class AiService:
    def __init__(self) -> None:
        model = os.environ.get("NETVUE_AI_MODEL", "/app/yolov8n.onnx")
        confidence = float(os.environ.get("NETVUE_AI_CONFIDENCE", "0.38"))
        fps = float(os.environ.get("NETVUE_AI_FPS", "3"))
        confirmation = int(os.environ.get("NETVUE_AI_CONFIRMATION_FRAMES", "3"))
        _LOGGER.info("Loading YOLOv8n model with ONNX Runtime CPU")
        self.detector = YoloDetector(model, confidence)
        _LOGGER.info("AI model ready: confidence=%.2f fps=%.1f confirmation_frames=%d",
                     confidence, fps, confirmation)
        self.fps = fps
        self.confirmation = confirmation
        self.zones = load_zones()
        self.workers: dict[str, CameraWorker] = {}
        self.config_dir = os.environ.get("NETVUE_AI_CONFIG_DIR", "/data")
        os.makedirs(self.config_dir, exist_ok=True)
        self.config_path = os.path.join(self.config_dir, "netvue-ai-config.json")
        self.configs = self._load_configs()
        self.events_path = os.path.join(self.config_dir, "netvue-ai-events.json")
        self.event_store = EventStore(self.events_path)
        self.rules_path = os.path.join(self.config_dir, "netvue-ai-rules.json")
        self.rules = self._load_rules()
        self._lock = threading.Lock()
        self.stop_event = threading.Event()

    def _load_configs(self) -> dict[str, dict]:
        try:
            with open(self.config_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            _LOGGER.info("No saved AI configuration; using defaults")
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            _LOGGER.error("Cannot load saved AI configuration: %s", exc)
            return {}
        return data if isinstance(data, dict) else {}

    def save_configs(self) -> None:
        temporary = f"{self.config_path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.configs, handle, ensure_ascii=False, sort_keys=True)
        os.replace(temporary, self.config_path)

    def _load_rules(self) -> dict[str, list[dict]]:
        try:
            with open(self.rules_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_rules(self) -> None:
        temporary = f"{self.rules_path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.rules, handle, ensure_ascii=False, sort_keys=True)
        os.replace(temporary, self.rules_path)

    def discover_stream_names(self) -> list[str]:
        configured = os.environ.get("NETVUE_AI_STREAM_NAMES", "").strip()
        if configured:
            return [value.strip() for value in configured.split(",") if value.strip()]
        try:
            with urllib.request.urlopen("http://127.0.0.1:9997/v3/paths/list", timeout=3) as response:
                data = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            _LOGGER.warning("Cannot discover MediaMTX streams: %s", exc)
            return []
        names = []
        for item in data.get("items", []):
            if item.get("ready") and isinstance(item.get("name"), str):
                names.append(item["name"])
        return names

    def reconcile(self) -> None:
        names = set(self.discover_stream_names())
        with self._lock:
            for name in names - set(self.workers):
                worker = CameraWorker(
                    name, self.zones.get(name, {}), self.detector,
                    self.fps, self.confirmation,
                )
                worker.event_store = self.event_store
                worker.rules = self.rules.get(name, [])
                for event in reversed(self.event_store.events):
                    if event.get("stream_name") == name and event.get("package_state") == "disappeared":
                        break
                    if event.get("stream_name") == name and event.get("package_state") == "deposited":
                        worker.package_states[event.get("track_id", 0)] = {
                            "track_id": event.get("track_id", 0),
                            "state": "present",
                            "first_seen": event.get("timestamp"),
                            "last_seen": event.get("timestamp"),
                            "zone": event.get("zone"),
                            "confidence": event.get("confidence", 0),
                        }
                self.workers[name] = worker
                config = self.configs.get(name)
                if config:
                    worker.set_enabled(config["enabled"])
                    worker.zones = config["zones"]
                    worker.interval = 1.0 / config["fps"]
                    worker._active_interval = max(
                        worker.interval, QUIET_INFERENCE_INTERVAL
                    )
                    worker.tracker.confirmation_frames = config["confirmation_frames"]
                    worker.confidence = config["confidence"]
                worker.start()
            for name in set(self.workers) - names:
                self.workers[name].stop()
                del self.workers[name]

    def snapshot(self) -> dict:
        with self._lock:
            cameras = {name: worker.snapshot() for name, worker in self.workers.items()}
        return {
            "state": "healthy" if cameras else "no_streams",
            "engine": "onnxruntime-yolov8n-cpu",
            "model": "COCO YOLOv8n",
            "package_detection": "unsupported",
            "cameras": cameras,
        }


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _LOGGER.info("Starting Netvue AI service")
    service = AiService()
    app = web.Application(client_max_size=1024)

    async def health(_request):
        return web.json_response(service.snapshot())

    async def status(request):
        name = request.match_info["stream_name"]
        with service._lock:
            worker = service.workers.get(name)
        if worker is None:
            return web.json_response({"error": "camera not found"}, status=404)
        return web.json_response(worker.snapshot())

    async def set_enabled(request):
        name = request.match_info["stream_name"]
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid request"}, status=400)
        enabled = body.get("enabled")
        if type(enabled) is not bool:
            return web.json_response({"error": "invalid request"}, status=400)
        with service._lock:
            worker = service.workers.get(name)
        if worker is None:
            return web.json_response({"error": "camera not found"}, status=404)
        worker.set_enabled(enabled)
        return web.json_response(worker.snapshot())

    async def set_config(request):
        name = request.match_info["stream_name"]
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid request"}, status=400)
        enabled = body.get("enabled")
        confidence = body.get("confidence")
        fps = body.get("fps")
        confirmation = body.get("confirmation_frames")
        zones = body.get("zones")
        if (type(enabled) is not bool
                or type(confidence) not in (int, float)
                or not 0.1 <= confidence <= 0.95
                or type(fps) not in (int, float)
                or not 1 <= fps <= 10
                or type(confirmation) is not int
                or not 1 <= confirmation <= 10
                or not isinstance(zones, dict)):
            return web.json_response({"error": "invalid request"}, status=400)
        clean_zones = {}
        for zone_name, rect in zones.items():
            if (zone_name not in ZONE_NAMES or not isinstance(rect, list)
                    or len(rect) != 4
                    or not all(type(value) in (int, float)
                               and 0 <= value <= 1 for value in rect)):
                return web.json_response({"error": "invalid request"}, status=400)
            clean_zones[zone_name] = rect
        config = {
            "enabled": enabled,
            "confidence": float(confidence),
            "fps": float(fps),
            "confirmation_frames": confirmation,
            "zones": clean_zones,
        }
        with service._lock:
            changed = service.configs.get(name) != config
            service.configs[name] = config
            service.save_configs()
            worker = service.workers.get(name)
            if worker is not None:
                worker.set_enabled(enabled)
                worker.zones = clean_zones
                worker.interval = 1.0 / float(fps)
                worker._active_interval = max(
                    worker.interval, QUIET_INFERENCE_INTERVAL
                )
                worker.tracker.confirmation_frames = confirmation
                worker.confidence = float(confidence)
        if changed:
            _LOGGER.info(
                "AI settings updated for %s: enabled=%s confidence=%.2f fps=%.1f zones=%d",
                name, enabled, confidence, fps, len(clean_zones),
            )
        if worker is None:
            service.reconcile()
            with service._lock:
                worker = service.workers.get(name)
        if worker is None:
            return web.json_response({"error": "camera not found"}, status=404)
        return web.json_response(worker.snapshot())

    async def events(request):
        name = request.match_info["stream_name"]
        with service._lock:
            worker = service.workers.get(name)
        since = request.query.get("since")
        events = list(service.event_store.events)
        if worker is not None:
            events = [event for event in events if event.get("stream_name") == name]
            events.extend([
                event for event in worker.events
                if event not in events
            ])
        if since:
            events = [event for event in events if event["id"] > since]
        return web.json_response({"events": events[-200:]})

    async def get_rules(request):
        name = request.match_info["stream_name"]
        with service._lock:
            rules = service.rules.get(name, [])
        return web.json_response({"rules": rules})

    async def set_rules(request):
        name = request.match_info["stream_name"]
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid request"}, status=400)
        rules = body.get("rules")
        if not isinstance(rules, list) or len(rules) > 100:
            return web.json_response({"error": "invalid rules"}, status=400)
        clean = []
        supported = {0, 14, 15, 16, 2, 5, 7, 1, 3}
        category_names = {value for key, value in COCO_NAMES.items() if key in supported}
        for rule in rules:
            if not isinstance(rule, dict):
                return web.json_response({"error": "invalid rule"}, status=400)
            category = rule.get("category", "*")
            zone = rule.get("zone")
            enabled = rule.get("enabled")
            days = rule.get("days")
            if (category not in ("*", *category_names)
                    or type(enabled) is not bool
                    or not isinstance(days, list)
                    or not all(type(day) is int and 0 <= day <= 6 for day in days)
                    or zone not in (None, "", *ZONE_NAMES)):
                return web.json_response({"error": "invalid rule"}, status=400)
            start = str(rule.get("start", "00:00"))
            end = str(rule.get("end", "23:59"))
            try:
                time.strptime(start, "%H:%M")
                time.strptime(end, "%H:%M")
            except ValueError:
                return web.json_response({"error": "invalid schedule"}, status=400)
            clean.append({
                "name": str(rule.get("name", ""))[:100],
                "enabled": enabled,
                "category": category,
                "zone": zone,
                "days": days,
                "start": start,
                "end": end,
                "confidence": float(rule.get("confidence", 0.38)),
                "confirmation_frames": int(rule.get("confirmation_frames", 3)),
                "pre_event": PRE_EVENT_SECONDS,
                "post_event": POST_EVENT_SECONDS,
                "notification": bool(rule.get("notification", True)),
            })
        with service._lock:
            service.rules[name] = clean
            service.save_rules()
            worker = service.workers.get(name)
            if worker is not None:
                worker.rules = clean
        return web.json_response({"rules": clean})

    app.router.add_get("/healthz", health)
    app.router.add_get("/api/v1/status", health)
    app.router.add_get("/api/v1/cameras/{stream_name}/status", status)
    app.router.add_put("/api/v1/cameras/{stream_name}/enabled", set_enabled)
    app.router.add_put("/api/v1/cameras/{stream_name}/config", set_config)
    app.router.add_get("/api/v1/cameras/{stream_name}/events", events)
    app.router.add_get("/api/v1/cameras/{stream_name}/rules", get_rules)
    app.router.add_put("/api/v1/cameras/{stream_name}/rules", set_rules)

    async def reconciliation_loop() -> None:
        service.reconcile()
        while not service.stop_event.is_set():
            await asyncio.sleep(10)
            service.reconcile()

    task = asyncio.create_task(reconciliation_loop())
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8766)
    await site.start()
    _LOGGER.info("Netvue AI API listening on 127.0.0.1:8766; waiting for camera streams")
    stop = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(signum, stop.set)
    await stop.wait()
    _LOGGER.info("Stopping Netvue AI service")
    service.stop_event.set()
    task.cancel()
    for worker in list(service.workers.values()):
        worker.stop()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
