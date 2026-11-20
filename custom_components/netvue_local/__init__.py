"""Netvue Local integration for an existing local bridge."""

import asyncio
import logging

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.setup import async_when_setup

from .bridge_api import BridgeError, async_stop_camera
from .const import CONF_HOST, CONF_STREAM_NAME, CONF_STREAM_URL, DOMAIN
from .coordinator import NetvueCoordinator
from .rtsp import stream_url_from_name

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CAMERA, Platform.BINARY_SENSOR, Platform.SENSOR, Platform.BUTTON]

FRONTEND_URL = "/netvue_local/frontend"
CARD_MODULE = "netvue-camera-card.js"
_FRONTEND_DIR = Path(__file__).parent / "frontend"
_STATIC_PATH_KEY = f"{DOMAIN}_frontend_static_path_registered"
_FRONTEND_MODULE_REQUESTED_KEY = f"{DOMAIN}_frontend_module_requested"
_FRONTEND_MODULE_REGISTERED_KEY = f"{DOMAIN}_frontend_module_registered"


async def _async_register_frontend(hass: HomeAssistant, _component: str) -> None:
    if hass.data.get(_FRONTEND_MODULE_REGISTERED_KEY):
        return

    from homeassistant.components import frontend

    version = (await async_get_integration(hass, DOMAIN)).version
    frontend.add_extra_js_url(hass, f"{FRONTEND_URL}/{CARD_MODULE}?v={version}")
    hass.data[_FRONTEND_MODULE_REGISTERED_KEY] = True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the Netvue Lovelace card once per Home Assistant instance."""
    if not hass.data.get(_STATIC_PATH_KEY):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_URL, str(_FRONTEND_DIR), cache_headers=False)]
        )
        hass.data[_STATIC_PATH_KEY] = True

    if "frontend" in hass.config.components:
        await _async_register_frontend(hass, "frontend")
    elif not hass.data.get(_FRONTEND_MODULE_REQUESTED_KEY):
        hass.data[_FRONTEND_MODULE_REQUESTED_KEY] = True
        # Frontend is optional; wait for it if it loads after this integration.
        async_when_setup(hass, "frontend", _async_register_frontend)
    return True


def _managed_camera(entry: ConfigEntry) -> tuple[str, str]:
    host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST))
    url = entry.options.get(CONF_STREAM_URL, entry.data.get(CONF_STREAM_URL))
    name = entry.options.get(CONF_STREAM_NAME, entry.data.get(CONF_STREAM_NAME))
    if not isinstance(host, str) or not isinstance(name, str):
        raise ValueError("Camera entry must contain the current managed stream settings")
    if url != stream_url_from_name(name):
        raise ValueError("Camera entry contains an unmanaged RTSP URL")
    return host, name


_LEGACY_OPTIONS = ("webrtc_enabled", "experimental_talkback")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # The unified card is the only interactive player: drop the retired player options.
    if any(key in entry.options for key in _LEGACY_OPTIONS):
        hass.config_entries.async_update_entry(entry, options={
            key: value for key, value in entry.options.items() if key not in _LEGACY_OPTIONS
        })
    try:
        camera = _managed_camera(entry)
    except (KeyError, ValueError) as exc:
        raise ConfigEntryNotReady(
            "Camera entry uses an unsupported configuration; remove and add it again"
        ) from exc
    coordinator = NetvueCoordinator(hass, entry, *camera)
    try:
        await coordinator.camera.async_reconcile()
    except BridgeError as exc:
        raise ConfigEntryNotReady("Netvue Bridge session manager is unavailable") from exc
    await coordinator.async_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    camera = _managed_camera(entry)
    unloaded = []
    cancelled = False

    async def finish(operation):
        nonlocal cancelled
        task = asyncio.ensure_future(operation)
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True

    try:
        for platform in PLATFORMS:
            result = await finish(hass.config_entries.async_forward_entry_unload(entry, platform))
            if result:
                unloaded.append(platform)
            if cancelled:
                raise asyncio.CancelledError
            if not result:
                _LOGGER.warning("Unable to unload all Netvue platforms; the bridge session was retained")
                break
        else:
            await finish(async_stop_camera(hass, camera[0]))
            # Once deletion is confirmed, complete the unload rather than
            # restoring an active entry whose bridge session no longer exists.
            return True
    except BaseException as exc:
        if unloaded:
            await finish(hass.config_entries.async_forward_entry_setups(entry, unloaded))
        if cancelled:
            raise asyncio.CancelledError from exc
        if not isinstance(exc, BridgeError):
            raise
        _LOGGER.warning("Unable to stop the bridge session while unloading the entry: %s", exc)
        return False
    if unloaded:
        await finish(hass.config_entries.async_forward_entry_setups(entry, unloaded))
    if cancelled:
        raise asyncio.CancelledError
    return False
