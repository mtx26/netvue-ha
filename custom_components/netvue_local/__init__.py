"""Netvue Local integration for an existing local bridge."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.core import HomeAssistant

from .bridge_api import BridgeError, async_ensure_camera, async_stop_camera
from .const import CONF_HOST, CONF_STREAM_NAME, CONF_STREAM_URL
from .coordinator import NetvueCoordinator
from .rtsp import stream_name_from_url, stream_url_from_name

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CAMERA, Platform.BINARY_SENSOR]


def _managed_camera(entry: ConfigEntry) -> tuple[str, str] | None:
    host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST))
    url = entry.options.get(CONF_STREAM_URL, entry.data.get(CONF_STREAM_URL))
    name = entry.options.get(CONF_STREAM_NAME, entry.data.get(CONF_STREAM_NAME))
    if name is None and isinstance(url, str):
        try:
            name = stream_name_from_url(url)
        except ValueError:
            return None
    if (isinstance(host, str) and isinstance(name, str)
            and url == stream_url_from_name(name)):
        return host, name
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    camera = _managed_camera(entry)
    if camera is not None:
        try:
            await async_ensure_camera(hass, *camera)
        except BridgeError as exc:
            raise ConfigEntryNotReady("Netvue Bridge session manager is unavailable") from exc
    coordinator = NetvueCoordinator(hass, entry)
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    camera = _managed_camera(entry)
    if unloaded and camera is not None:
        try:
            await async_stop_camera(hass, camera[0])
        except BridgeError as exc:
            _LOGGER.warning("Unable to stop the bridge session while unloading the entry: %s", exc)
    return unloaded
