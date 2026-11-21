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

COCO_NAMES = {
    0: "person", 14: "bird", 15: "cat", 16: "dog",
    2: "car", 5: "bus", 7: "truck", 1: "bicycle", 3: "motorcycle",
}
TRACKED_CLASSES = set(COCO_NAMES)
ZONE_NAMES = {"entrance", "doorstep", "garden", "driveway"}
_LOGGER = logging.getLogger(__name__)


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
        detections = list(detections)
        unmatched_tracks = set(self.tracks)
        matched: list[Track] = []
        for detection in detections:
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
            if self.age - self.last_seen.get(track_id, 0) > 5:
                self.tracks.pop(track_id, None)
                self.last_seen.pop(track_id, None)
        return matched


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
        resized = cv2.resize(rgb, (self.input_size, self.input_size))
        tensor = resized.transpose(2, 0, 1)[np.newaxis].astype(np.float32) / 255.0
        output = self.session.run(None, {self.input_name: tensor})[0]
        predictions = np.asarray(output).squeeze()
        if predictions.ndim == 1:
            predictions = predictions[np.newaxis, :]
        if predictions.shape[0] == 84:
            predictions = predictions.T

        detections: list[Detection] = []
        for row in predictions:
            if len(row) < 6:
                continue
            class_id = int(row[4:].argmax())
            score = float(row[4:][class_id])
            threshold = self.confidence if confidence is None else confidence
            if class_id not in TRACKED_CLASSES or score < threshold:
                continue
            cx, cy, bw, bh = (float(value) for value in row[:4])
            box = (
                max(0.0, cx - bw / 2) / self.input_size * width,
                max(0.0, cy - bh / 2) / self.input_size * height,
                min(float(width), cx + bw / 2) / self.input_size * width,
                min(float(height), cy + bh / 2) / self.input_size * height,
            )
            detections.append(Detection(class_id, COCO_NAMES[class_id], score, box))
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
        }
        self.confidence = detector.confidence
        self._cooldowns: dict[str, float] = {}
        self._lock = threading.Lock()

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            changed = self.status["enabled"] != enabled
            self.status["enabled"] = enabled
        if changed:
            _LOGGER.info("AI %s for %s", "enabled" if enabled else "disabled", self.stream_name)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.status)

    def emit(self, event_type: str, label: str, confidence: float,
             track_id: int, zone: str | None = None) -> None:
        now = time.time()
        key = f"{track_id}:{label}:{zone or 'camera'}"
        if now - self._cooldowns.get(key, 0) < 15:
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
                    if now - last_inference < self.interval:
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
                    detections = self.detector.detect(frame, self.confidence)
                    confirmed = self.tracker.update(detections)
                    latest = [
                        {"category": item.label, "confidence": round(item.confidence, 3)}
                        for item in confirmed
                    ]
                    with self._lock:
                        self.status["state"] = "detecting"
                        self.status["detections"] = latest
                        self.status["last_frame_at"] = time.time()
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
        confidence = float(os.environ.get("NETVUE_AI_CONFIDENCE", "0.45"))
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
                self.workers[name] = worker
                config = self.configs.get(name)
                if config:
                    worker.set_enabled(config["enabled"])
                    worker.zones = config["zones"]
                    worker.interval = 1.0 / config["fps"]
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
        if worker is None:
            return web.json_response({"events": []})
        since = request.query.get("since")
        events = worker.events
        if since:
            events = [event for event in events if event["id"] > since]
        return web.json_response({"events": events[-200:]})

    app.router.add_get("/healthz", health)
    app.router.add_get("/api/v1/status", health)
    app.router.add_get("/api/v1/cameras/{stream_name}/status", status)
    app.router.add_put("/api/v1/cameras/{stream_name}/enabled", set_enabled)
    app.router.add_put("/api/v1/cameras/{stream_name}/config", set_config)
    app.router.add_get("/api/v1/cameras/{stream_name}/events", events)

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
