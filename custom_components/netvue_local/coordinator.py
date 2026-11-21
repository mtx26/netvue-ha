"""Monitor RTSP availability without contacting the camera."""

from collections import deque
from datetime import timedelta
import logging
from typing import NamedTuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

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
    CONF_AI_FPS, CONF_AI_ZONES, CONF_STREAM_NAME, CONF_STREAM_URL,
    DOMAIN, UPDATE_INTERVAL,
)
from .rtsp import async_probe_stream

_LOGGER = logging.getLogger(__name__)


class CameraData(NamedTuple):
    session: SessionStatus | None
    rtsp_available: bool
    manager_error: str | None = None
    recording: RecordingStatus | None = None
    detection: DetectionStatus | None = None
    ai_error: str | None = None


class NetvueCoordinator(DataUpdateCoordinator[CameraData]):
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, host: str, name: str
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=UPDATE_INTERVAL))
        self.url = entry.options.get(CONF_STREAM_URL, entry.data[CONF_STREAM_URL])
        self.camera = ManagedCamera(hass, host, name)
        self._entry = entry
        self._device_metadata: dict[str, str] = {}
        self._last_detection_event_ids: deque[str] = deque(maxlen=200)

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
                        self._entry.data.get(CONF_AI_CONFIDENCE, 0.45),
                    ),
                    fps=self._entry.options.get(
                        CONF_AI_FPS, self._entry.data.get(CONF_AI_FPS, 3)
                    ),
                    confirmation_frames=self._entry.options.get(
                        CONF_AI_CONFIRMATION_FRAMES,
                        self._entry.data.get(CONF_AI_CONFIRMATION_FRAMES, 3),
                    ),
                    zones=self._entry.options.get(
                        CONF_AI_ZONES, self._entry.data.get(CONF_AI_ZONES, {})
                    ),
                )
            detection = await async_get_detection_status(self.hass, stream_name)
        except (BridgeError, KeyError) as exc:
            ai_error = str(exc)
            _LOGGER.debug("Unable to read local AI detection status: %s", exc)
        if detection is not None:
            await self._async_fire_new_detection_events(detection)
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
        for event in events:
            event_id = event.get("id") if isinstance(event, dict) else None
            if not isinstance(event_id, str) or event_id in self._last_detection_event_ids:
                continue
            self._last_detection_event_ids.append(event_id)
            self.hass.bus.async_fire(
                f"{DOMAIN}_event",
                {
                    **event,
                    "entry_id": self._entry.entry_id,
                    "camera": self._entry.title,
                    "host": self.camera.host,
                },
            )


class NetvueEntity(CoordinatorEntity[NetvueCoordinator]):
    """Share entry-based identity and current descriptive device information."""

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info
