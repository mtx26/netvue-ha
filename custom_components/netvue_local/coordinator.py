"""Monitor RTSP availability without contacting the camera."""

from datetime import timedelta
import logging
from typing import NamedTuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .bridge_api import BridgeError, ManagedCamera, SessionStatus
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

    async def _async_update_data(self) -> CameraData:
        session = None
        manager_error = None
        try:
            session = await self.camera.async_reconcile()
        except BridgeError as exc:
            manager_error = str(exc)
            _LOGGER.warning("Unable to reconcile the bridge camera: %s", exc)
        return CameraData(session, await async_probe_stream(self.url), manager_error)
