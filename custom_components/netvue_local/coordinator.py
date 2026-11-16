"""Monitor RTSP availability without contacting the camera."""

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_STREAM_URL, DOMAIN, UPDATE_INTERVAL
from .rtsp import async_probe_stream

_LOGGER = logging.getLogger(__name__)


class NetvueCoordinator(DataUpdateCoordinator[bool]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=UPDATE_INTERVAL))
        self.url = entry.options.get(CONF_STREAM_URL, entry.data[CONF_STREAM_URL])

    async def _async_update_data(self) -> bool:
        return await async_probe_stream(self.url)
