"""Monitor RTSP availability without contacting the camera."""

from datetime import timedelta
import logging
from typing import NamedTuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .bridge_api import BridgeError, CameraMetadata, ManagedCamera, SessionStatus
from .const import CONF_STREAM_URL, DOMAIN, UPDATE_INTERVAL
from .rtsp import async_probe_stream

_LOGGER = logging.getLogger(__name__)


class CameraData(NamedTuple):
    session: SessionStatus | None
    rtsp_available: bool
    manager_error: str | None = None


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
        try:
            session = await self.camera.async_reconcile()
        except BridgeError as exc:
            manager_error = str(exc)
            _LOGGER.warning("Unable to reconcile the bridge camera: %s", exc)
        if session is not None and session.metadata is not None:
            self._update_device_metadata(session.metadata)
        return CameraData(session, await async_probe_stream(self.url), manager_error)


class NetvueEntity(CoordinatorEntity[NetvueCoordinator]):
    """Share entry-based identity and current descriptive device information."""

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info
