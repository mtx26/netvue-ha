"""Local RTSP object detection service for Netvue cameras."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import signal
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterable
from collections.abc import Iterator
from uuid import uuid4

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
ZONE_ID_PATTERN = re.compile(r"[a-z0-9_-]{1,64}")
ZONE_CONFIRMATION_FRAMES = 3
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
class ZonePresence:
    last_seen: float
    presence_id: str = field(default_factory=lambda: uuid4().hex)

    def expired(self, timestamp: float, observed: bool) -> bool:
        idle_for = timestamp - self.last_seen
        return (idle_for > DETECTION_GRACE_PERIOD
                or (not observed and idle_for >= DETECTION_GRACE_PERIOD))



@dataclass
class Track:
    track_id: int
    label: str
    box: tuple[float, float, float, float]
    confidence: float = 0.0
    confirmed_frames: int = 1
    zone_hits: dict[str, int] = field(default_factory=dict)
    active_zones: dict[str, ZonePresence] = field(default_factory=dict)
    last_seen_frame: int = 0

    def update_zones(self, observed_zones: Iterable[str], changed_zones: set[str],
                     timestamp: float) -> Iterable[str]:
        for zone_id in changed_zones:
            self.zone_hits.pop(zone_id, None)
            self.active_zones.pop(zone_id, None)

        hits = {zone_id: self.zone_hits.get(zone_id, 0) + 1 for zone_id in observed_zones}
        for zone_id, presence in list(self.active_zones.items()):
            if presence.expired(timestamp, zone_id in hits):
                del self.active_zones[zone_id]
                if zone_id in hits:
                    hits[zone_id] = 1

        self.zone_hits = hits
        for zone_id, count in hits.items():
            presence = self.active_zones.get(zone_id)
            if presence is not None:
                presence.last_seen = timestamp
            elif count >= ZONE_CONFIRMATION_FRAMES:
                self.active_zones[zone_id] = ZonePresence(timestamp)
                yield zone_id


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
    def __init__(self, confirmation_frames: int = 3, missed_frames: int = 5) -> None:
        self.confirmation_frames = confirmation_frames
        self.missed_frames = missed_frames
        self.tracks: dict[int, Track] = {}
        self.next_id = 1
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
            track.last_seen_frame = self.age
            if track.confirmed_frames >= self.confirmation_frames:
                matched.append(track)
        for track_id in unmatched_tracks:
            if self.age - self.tracks[track_id].last_seen_frame > self.missed_frames:
                del self.tracks[track_id]
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


@dataclass(frozen=True, slots=True)
class Zone:
    name: str
    points: tuple[tuple[float, float], ...]

    @classmethod
    def from_config(cls, zone_id: str, value: object) -> Zone | None:
        if isinstance(value, list):
            if (len(value) != 4 or not all(_coordinate(number) for number in value)
                    or not (value[0] < value[2] and value[1] < value[3])):
                return None
            left, top, right, bottom = value
            return cls(zone_id, ((left, top), (right, top), (right, bottom), (left, bottom)))

        if not isinstance(value, dict) or not isinstance(name := value.get("name"), str):
            return None
        if not name.strip() or len(name.strip()) > 80:
            return None
        points = value.get("points")
        if (not isinstance(points, list) or not 3 <= len(points) <= 32
                or not all(isinstance(point, list) and len(point) == 2
                           and all(_coordinate(number) for number in point) for point in points)):
            return None
        contour = tuple((x, y) for x, y in points)
        if len(set(contour)) != len(contour):
            return None
        area = sum(x * next_y - next_x * y
                   for (x, y), (next_x, next_y) in zip(contour, contour[1:] + contour[:1]))
        if abs(area) < 1e-8:
            return None
        return cls(name, contour)

    def to_config(self) -> dict:
        return {"name": self.name, "points": [list(point) for point in self.points]}

    def contains(self, box: tuple[float, float, float, float],
                 frame_size: tuple[int, int]) -> bool:
        height, width = frame_size
        cx = (box[0] + box[2]) / 2 / width
        cy = (box[1] + box[3]) / 2 / height
        inside = False
        x1, y1 = self.points[-1]
        for x2, y2 in self.points:
            cross = (cx - x1) * (y2 - y1) - (cy - y1) * (x2 - x1)
            if (abs(cross) < 1e-9 and min(x1, x2) <= cx <= max(x1, x2)
                    and min(y1, y2) <= cy <= max(y1, y2)):
                return True
            if (y1 > cy) != (y2 > cy) and cx < x1 + (cy - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
            x1, y1 = x2, y2
        return inside


def _coordinate(value):
    return type(value) in (int, float) and 0 <= value <= 1


def parse_zones(value: object) -> dict[str, Zone] | None:
    if not isinstance(value, dict) or len(value) > 32:
        return None
    zones = {}
    for zone_id, config in value.items():
        if not isinstance(zone_id, str) or not ZONE_ID_PATTERN.fullmatch(zone_id):
            return None
        zone = Zone.from_config(zone_id, config)
        if zone is None:
            return None
        zones[zone_id] = zone
    return zones


def load_zones() -> dict[str, dict[str, Zone]]:
    try:
        zones = json.loads(os.environ.get("NETVUE_AI_ZONES", "{}"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(zones, dict):
        return {}
    return {stream: parsed for stream, config in zones.items()
            if (parsed := parse_zones(config)) is not None}


class CameraWorker:
    def __init__(self, stream_name: str, zones: dict[str, Zone],
                 detector: YoloDetector, fps: float, confirmation: int) -> None:
        self.stream_name = stream_name
        self._zones = zones
        self._changed_zones: set[str] = set()
        self.detector = detector
        self.interval = 1.0 / max(1.0, fps)
        self._active_interval = max(self.interval, QUIET_INFERENCE_INTERVAL)
        self._last_detection_at: float | None = None
        self._metadata_has_boxes = False
        self.tracker = IoUTracker(confirmation, max(5, math.ceil(fps * DETECTION_GRACE_PERIOD)))
        self.events: list[dict] = []
        self.event_cursor = 0
        self.running = True
        self.thread = threading.Thread(target=self.run, name=f"ai-{stream_name}", daemon=True)
        self.status = {
            "stream_name": stream_name,
            "enabled": True,
            "state": "starting",
            "detections": [],
            "zone_tracking": True,
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

    def apply_config(self, config: dict) -> None:
        zones = config["zones"]
        fps = config["fps"]
        with self._lock:
            self._changed_zones.update(
                zone_id for zone_id, zone in self._zones.items()
                if zone_id not in zones or zone.points != zones[zone_id].points
            )
            self._zones = zones
            self.interval = 1.0 / fps
            self._active_interval = max(self.interval, QUIET_INFERENCE_INTERVAL)
            self.tracker.confirmation_frames = config["confirmation_frames"]
            self.tracker.missed_frames = max(5, math.ceil(fps * DETECTION_GRACE_PERIOD))
            self.confidence = config["confidence"]
        self.set_enabled(config["enabled"])

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

    def _matching_rules(self, track: Track, zone_ids: list[str], timestamp: float) -> list[dict]:
        matches = []
        for rule in self.rules:
            if not rule.get("enabled", True):
                continue
            if rule.get("category") not in ("", "*", track.label):
                continue
            if rule.get("zone") and rule["zone"] not in zone_ids:
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
        with self._lock:
            self.events.append(event)
            del self.events[:-1000]
            self.status["last_event_at"] = time.time()
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
                             timestamp: float, memberships: dict[int, list[str]]) -> None:
        packages = [track for track in visible if track.label in ("package", "parcel", "box")]
        seen: set[int] = set()
        for track in packages:
            seen.add(track.track_id)
            zone = next(iter(memberships[track.track_id]), None)
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
                         timestamp: float, memberships: dict[int, list[str]]) -> None:
        for track in visible:
            zone_ids = memberships[track.track_id]
            zone = next(iter(zone_ids), None)
            key = (track.label, track.track_id)
            active = self.active_lifecycle.get(key)
            if active is None:
                matches = self._matching_rules(track, zone_ids, timestamp)
                if track.confirmed_frames < self.tracker.confirmation_frames:
                    continue
                if matches:
                    zone = matches[0].get("zone") or zone
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
                    matches = self._matching_rules(track, zone_ids, timestamp)
                    if matches:
                        active["confirmed"] = True
                        active["rule"] = matches[0]
                        zone = matches[0].get("zone") or zone
                        active["zone"] = zone
                        self._persist_lifecycle_event("event_confirmed", track, zone, timestamp, active["rule"])

        grace = max(DETECTION_GRACE_PERIOD, 3.0)
        for key, active in list(self.active_lifecycle.items()):
            if timestamp - active["last_seen"] <= grace:
                continue
            if active["confirmed"]:
                track = active["track"]
                zone = active["zone"]
                self._persist_lifecycle_event("event_ended", track, zone, timestamp, active["rule"])
                if active["rule"] is not None:
                    self._protect_lifecycle_range(active["start"], timestamp, track, zone, active["rule"])
            del self.active_lifecycle[key]

    def update_zones(self, visible: list[Track], matched: list[Track],
                     timestamp: float, frame_size: tuple[int, int]
                     ) -> tuple[dict[int, list[str]], list[tuple[Track, str]]]:
        with self._lock:
            zones = self._zones
            changed = self._changed_zones
            self._changed_zones = set()
        matched_ids = {track.track_id for track in matched}
        memberships = {}
        transitions = []
        for track in visible:
            observed_zones = (
                (zone_id for zone_id, zone in zones.items() if zone.contains(track.box, frame_size))
                if track.track_id in matched_ids else ()
            )
            transitions.extend(
                (track, zone_id)
                for zone_id in track.update_zones(observed_zones, changed, timestamp)
            )
            memberships[track.track_id] = sorted(track.active_zones)
        return memberships, transitions

    def emit(self, event_type: str, label: str, confidence: float,
             track_id: int, zone: str | None = None, *,
             zone_ids: list[str] | None = None, presence_id: str | None = None) -> None:
        now = time.time()
        if zone_ids is None:
            key = f"{event_type}:{label}:{zone or 'camera'}"
            if now - self._cooldowns.get(key, 0) < EVENT_COOLDOWN_SECONDS:
                return
            self._cooldowns[key] = now
        event = {
            "id": f"{int(now * 1000)}-{self.stream_name}-{track_id}-{uuid4().hex}",
            "timestamp": now,
            "stream_name": self.stream_name,
            "event": event_type,
            "category": label,
            "confidence": round(confidence, 3),
            "track_id": track_id,
            "zone": zone,
        }
        if zone_ids is not None:
            event["zone_ids"] = zone_ids
        if presence_id is not None:
            event["presence_id"] = presence_id
        with self._lock:
            self.events.append(event)
            del self.events[:-1000]
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
            last_frame_key = None
            last_frame = None
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
                    frame = decoded.to_ndarray(format="bgr24")
                    frame_key = (decoded.pts, decoded.time_base) if decoded.pts is not None else None
                    if (frame_key is not None and frame_key == last_frame_key
                            and last_frame is not None and np.array_equal(frame, last_frame)):
                        with self._lock:
                            self.status["frames_skipped"] += 1
                        continue
                    last_inference = now
                    last_frame_key = frame_key
                    last_frame = frame if frame_key is not None else None
                    new_size = frame.shape[:2]
                    if new_size != frame_size:
                        _LOGGER.info("Video ready for %s: %dx%d", self.stream_name,
                                     new_size[1], new_size[0])
                        next_id = self.tracker.next_id
                        self.tracker = IoUTracker(
                            self.tracker.confirmation_frames, self.tracker.missed_frames
                        )
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
                        self.tracker = IoUTracker(
                            self.tracker.confirmation_frames, self.tracker.missed_frames
                        )
                        self.tracker.next_id = next_id
                        self.persist_metadata(time.time(), frame_size, ())
                        _LOGGER.warning("Inference failed for %s: %s", self.stream_name, exc)
                        continue
                    latency_ms = (time.perf_counter() - inference_started) * 1000
                    self._update_sampling_interval(now, bool(detections))
                    confirmed = self.tracker.update(detections)
                    visible = self.tracker.visible()
                    self._record_inference(now, latency_ms, bool(confirmed))
                    timestamp = time.time()
                    memberships, zone_transitions = self.update_zones(
                        visible, confirmed, now, frame_size
                    )
                    latest = [
                        {
                            "category": item.label,
                            "confidence": round(item.confidence, 3),
                            "track_id": item.track_id,
                            "zone_ids": memberships[item.track_id],
                            "zone_presence_ids": {
                                zone_id: presence.presence_id
                                for zone_id, presence in item.active_zones.items()
                            },
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
                    with self._lock:
                        self.status["state"] = "detecting"
                        self.status["detections"] = latest
                        self.status["last_frame_at"] = timestamp
                    for track, zone_id in zone_transitions:
                        self.emit(
                            "zone_entered",
                            track.label, track.confidence, track.track_id, zone_id,
                            zone_ids=memberships[track.track_id],
                            presence_id=track.active_zones[zone_id].presence_id,
                        )
                    for track in confirmed:
                        if not track.zone_hits:
                            self.emit("object_detected", track.label, track.confidence, track.track_id)
                    self.persist_metadata(timestamp, frame_size, visible)
                    self.update_lifecycle(visible, timestamp, memberships)
                    self.update_package_state(visible, timestamp, memberships)
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
        if not isinstance(data, dict):
            return {}
        return {
            stream: {**config, "zones": parse_zones(config.get("zones", {})) or {}}
            for stream, config in data.items() if isinstance(config, dict)
        }

    def save_configs(self) -> None:
        temporary = f"{self.config_path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            configs = {
                stream: {**config, "zones": {
                    zone_id: zone.to_config() for zone_id, zone in config["zones"].items()
                }}
                for stream, config in self.configs.items()
            }
            json.dump(configs, handle, ensure_ascii=False, sort_keys=True)
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
                    worker.apply_config(config)
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
        clean_zones = parse_zones(zones)
        if clean_zones is None:
            return web.json_response({"error": "invalid zones"}, status=400)
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
                worker.apply_config(config)
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
        events = {
            event["id"]: event for event in service.event_store.events
            if event.get("stream_name") == name and isinstance(event.get("id"), str)
        }
        if worker is not None:
            with worker._lock:
                events.update((event["id"], event) for event in worker.events)
        if since:
            events = {event_id: event for event_id, event in events.items() if event_id > since}
        ordered = sorted(events.values(), key=lambda event: (event["timestamp"], event["id"]))
        return web.json_response({"events": ordered[-1000:]})

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
        category_names = ("*", *COCO_NAMES.values())
        for rule in rules:
            if not isinstance(rule, dict):
                return web.json_response({"error": "invalid rule"}, status=400)
            category = rule.get("category", "*")
            zone = rule.get("zone")
            enabled = rule.get("enabled")
            days = rule.get("days")
            if (category not in category_names
                    or type(enabled) is not bool
                    or not isinstance(days, list)
                    or not all(type(day) is int and 0 <= day <= 6 for day in days)
                    or (zone is not None and (
                        not isinstance(zone, str) or (zone and not ZONE_ID_PATTERN.fullmatch(zone))
                    ))):
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
