"""Monitor RTSP availability without contacting the camera."""

import asyncio
from collections import deque
from datetime import timedelta
import logging
import time
from typing import NamedTuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity, DataUpdateCoordinator, UpdateFailed,
)

from .bridge_api import (
    BridgeError,
    CameraMetadata,
    DetectionStatus,
    ManagedCamera,
    RecordingStatus,
    SessionStatus,
    async_get_detection_events,
    async_get_detection_status,
    async_set_detection_config,
)
from .const import (
    CONF_AI_CONFIDENCE, CONF_AI_CONFIRMATION_FRAMES, CONF_AI_ENABLED,
    CONF_AI_FPS, CONF_NOTIFY_COOLDOWN, CONF_NOTIFY_TARGETS,
    CONF_STREAM_NAME, CONF_STREAM_URL,
    DEFAULT_AI_CONFIDENCE, DOMAIN, EVENT_DETECTION, UPDATE_INTERVAL,
)
from .rtsp import async_probe_stream
from .zones import configured_zones, fresh_zone_frame

_LOGGER = logging.getLogger(__name__)


class CameraData(NamedTuple):
    session: SessionStatus | None
    rtsp_available: bool
    manager_error: str | None = None
    recording: RecordingStatus | None = None
    detection: DetectionStatus | None = None
    ai_error: str | None = None


class NetvueLiveDetectionCoordinator(DataUpdateCoordinator[DetectionStatus]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, stream_name: str) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN} detection {stream_name}", config_entry=entry,
            update_interval=timedelta(seconds=2), always_update=False,
        )
        self.stream_name = stream_name

    async def _async_update_data(self) -> DetectionStatus:
        try:
            status = await async_get_detection_status(self.hass, self.stream_name)
        except BridgeError as exc:
            raise UpdateFailed(str(exc)) from exc
        if (status.zone_tracking and status.enabled and status.state == "detecting"
                and not fresh_zone_frame(status)):
            raise UpdateFailed("No recent AI detection frame")
        return status

    @property
    def zone_detection_available(self) -> bool:
        status = self.data
        return (self.last_update_success and status is not None and status.enabled
                and status.state == "detecting" and status.zone_tracking and fresh_zone_frame(status))

    def has_detection_in_zone(
        self, zone_id: str, categories: frozenset[str] | None = None
    ) -> bool:
        status = self.data
        if status is None or not self.zone_detection_available:
            return False
        for detection in status.detections:
            zone_ids = detection.get("zone_ids")
            if not isinstance(zone_ids, list) or zone_id not in zone_ids:
                continue
            category = detection.get("category")
            if categories is None or (isinstance(category, str) and category in categories):
                return True
        return False


class NetvueCoordinator(DataUpdateCoordinator[CameraData]):
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, host: str, name: str
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=UPDATE_INTERVAL))
        self.url = entry.options.get(CONF_STREAM_URL, entry.data[CONF_STREAM_URL])
        self.camera = ManagedCamera(hass, host, name)
        self.live_detection = NetvueLiveDetectionCoordinator(hass, entry, name)
        self._entry = entry
        self._device_metadata: dict[str, str] = {}
        self._last_detection_event_ids: deque[str] = deque(maxlen=1000)
        self._event_history: deque[dict] = deque(maxlen=1000)
        self._event_store = Store(hass, 1, f"{DOMAIN}_events_{entry.entry_id}")
        self._last_notification_by_key: dict[str, float] = {}
        self._last_polled_event_at: float | None = None
        self._event_task: asyncio.Task | None = None

    @callback
    def async_poll_live_events(self) -> None:
        detection = self.live_detection.data
        if (detection is None or detection.last_event_at is None
                or detection.last_event_at == self._last_polled_event_at
                or (self._event_task is not None and not self._event_task.done())):
            return
        self._event_task = self._entry.async_create_task(
            self.hass, self._async_fire_new_detection_events(detection), "netvue detection events"
        )

    async def async_load_event_history(self) -> None:
        stored = await self._event_store.async_load()
        if isinstance(stored, dict):
            history = stored.get("history")
            event_ids = stored.get("event_ids")
        else:
            history = stored
            event_ids = None
        if isinstance(history, list):
            self._event_history.extend(
                event for event in history[-1000:]
                if isinstance(event, dict) and isinstance(event.get("id"), str)
            )
        if isinstance(event_ids, list):
            self._last_detection_event_ids.extend(
                event_id for event_id in event_ids[-1000:] if isinstance(event_id, str)
            )
        else:
            self._last_detection_event_ids.extend(event["id"] for event in self._event_history)

    @property
    def event_history(self) -> list[dict]:
        return list(self._event_history)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title, manufacturer="Netvue", **self._device_metadata,
        )

    def _update_device_metadata(self, metadata: CameraMetadata) -> None:
        # Missing fields are unknown, not a request to erase last known registry data.
        if metadata.serial_number is not None:
            self._device_metadata["serial_number"] = metadata.serial_number
        if metadata.firmware_version is not None:
            self._device_metadata["sw_version"] = metadata.firmware_version
        registry = dr.async_get(self.hass)
        device = registry.async_get_device_by_identifier(
            (DOMAIN, self._entry.entry_id), self._entry.entry_id,
        )
        if device is not None:
            changes = {
                key: value for key, value in self._device_metadata.items()
                if getattr(device, key) != value
            }
            if changes:
                registry.async_update_device(device.id, **changes)

    async def _async_update_data(self) -> CameraData:
        session = None
        manager_error = None
        recording = None
        detection = None
        ai_error = None
        try:
            session = await self.camera.async_reconcile()
        except BridgeError as exc:
            manager_error = str(exc)
            _LOGGER.warning("Unable to reconcile the bridge camera: %s", exc)
        if session is not None:
            try:
                recording = await self.camera.async_recording_status()
            except BridgeError as exc:
                if manager_error is None:
                    manager_error = str(exc)
                _LOGGER.warning("Unable to read bridge recording status: %s", exc)
        if session is not None and session.metadata is not None:
            self._update_device_metadata(session.metadata)
        try:
            stream_name = self._entry.options.get(
                CONF_STREAM_NAME, self._entry.data[CONF_STREAM_NAME]
            )
            ai_configured = (
                CONF_AI_ENABLED in self._entry.options
                or CONF_AI_ENABLED in self._entry.data
            )
            if ai_configured:
                ai_enabled = self._entry.options.get(
                    CONF_AI_ENABLED, self._entry.data.get(CONF_AI_ENABLED)
                )
                await async_set_detection_config(
                    self.hass,
                    stream_name,
                    enabled=ai_enabled,
                    confidence=self._entry.options.get(
                        CONF_AI_CONFIDENCE,
                        self._entry.data.get(CONF_AI_CONFIDENCE, DEFAULT_AI_CONFIDENCE),
                    ),
                    fps=self._entry.options.get(
                        CONF_AI_FPS, self._entry.data.get(CONF_AI_FPS, 3)
                    ),
                    confirmation_frames=self._entry.options.get(
                        CONF_AI_CONFIRMATION_FRAMES,
                        self._entry.data.get(CONF_AI_CONFIRMATION_FRAMES, 3),
                    ),
                    zones=configured_zones(self._entry),
                )
            detection = await async_get_detection_status(self.hass, stream_name)
        except (BridgeError, KeyError) as exc:
            ai_error = str(exc)
            _LOGGER.debug("Unable to read local AI detection status: %s", exc)
        return CameraData(
            session,
            await async_probe_stream(self.url),
            manager_error,
            recording,
            detection,
            ai_error,
        )

    async def _async_fire_new_detection_events(
        self, detection: DetectionStatus
    ) -> None:
        if detection.last_event_at is None:
            return
        stream_name = self._entry.options.get(
            CONF_STREAM_NAME, self._entry.data[CONF_STREAM_NAME]
        )
        try:
            events = await async_get_detection_events(self.hass, stream_name)
        except BridgeError as exc:
            _LOGGER.debug("Unable to read local AI events: %s", exc)
            return
        new_events = []
        for event in events:
            event_id = event.get("id") if isinstance(event, dict) else None
            if not isinstance(event_id, str) or event_id in self._last_detection_event_ids:
                continue
            self._last_detection_event_ids.append(event_id)
            new_events.append(event)
            if event.get("event") != "zone_dwell":
                self._event_history.append(event)
        if new_events:
            await self._event_store.async_save({
                "history": list(self._event_history),
                "event_ids": list(self._last_detection_event_ids),
            })
        self._last_polled_event_at = detection.last_event_at
        for event in new_events:
            self.hass.bus.async_fire(
                EVENT_DETECTION,
                {
                    **event,
                    "entry_id": self._entry.entry_id,
                    "camera": self._entry.title,
                    "host": self.camera.host,
                },
            )
            await self._async_send_detection_notification(event)

    async def _async_send_detection_notification(self, event: dict) -> None:
        if event.get("event") not in ("event_confirmed", "package_deposited", "package_disappeared"):
            return
        targets = self._entry.options.get(
            CONF_NOTIFY_TARGETS, self._entry.data.get(CONF_NOTIFY_TARGETS, [])
        )
        if not isinstance(targets, list) or not targets:
            return
        cooldown = self._entry.options.get(
            CONF_NOTIFY_COOLDOWN, self._entry.data.get(CONF_NOTIFY_COOLDOWN, 30)
        )
        try:
            cooldown = max(0, int(cooldown))
        except (TypeError, ValueError):
            cooldown = 30
        key = f"{event.get('stream_name')}:{event.get('category')}:{event.get('zone')}"
        now = time.time()
        if now - self._last_notification_by_key.get(key, 0) < cooldown:
            return
        category = str(event.get("category") or "objet")
        zone = str(event.get("zone") or "Toute l'image")
        timestamp = time.strftime(
            "%d/%m/%Y %H:%M:%S", time.localtime(float(event.get("timestamp", now)))
        )
        title = f"Netvue — {category.capitalize()} détecté"
        message = f"{self._entry.title} · {timestamp} · Zone : {zone}"
        for target in targets:
            if not isinstance(target, str) or not target.strip():
                continue
            service = target.strip()
            if service.startswith("notify."):
                service = service[len("notify."):]
            try:
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {
                        "title": title,
                        "message": message,
                        "data": {
                            "tag": f"netvue-{event.get('id')}",
                            "group": "netvue",
                            "channel": "Netvue",
                            "clickAction": "/lovelace/netvue",
                        },
                    },
                    blocking=False,
                )
            except (KeyError, ValueError, RuntimeError) as exc:
                _LOGGER.warning("Unable to send Netvue notification to %s: %s", service, exc)
        self._last_notification_by_key[key] = now


class NetvueEntity(CoordinatorEntity[NetvueCoordinator]):
    """Share entry-based identity and current descriptive device information."""

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info


class NetvueLiveDetectionEntity(CoordinatorEntity[NetvueLiveDetectionCoordinator]):
    def __init__(self, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator.live_detection)
        self._camera_coordinator = coordinator

    @property
    def device_info(self) -> DeviceInfo:
        return self._camera_coordinator.device_info
