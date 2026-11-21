"""Netvue Local integration for an existing local bridge."""

import asyncio
import logging
from urllib.parse import quote

from pathlib import Path

from aiohttp import ClientError, ClientTimeout, web
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.http.view import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.setup import async_when_setup
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .bridge_api import API_URL, BridgeError, async_stop_camera
from .const import CONF_HOST, CONF_STREAM_NAME, CONF_STREAM_URL, DOMAIN
from .coordinator import NetvueCoordinator
from .rtsp import stream_url_from_name

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
]

FRONTEND_URL = "/netvue_local/frontend"
CARD_MODULE = "netvue-camera-card.js"
_FRONTEND_DIR = Path(__file__).parent / "frontend"
_STATIC_PATH_KEY = f"{DOMAIN}_frontend_static_path_registered"
_FRONTEND_MODULE_REQUESTED_KEY = f"{DOMAIN}_frontend_module_requested"
_FRONTEND_MODULE_REGISTERED_KEY = f"{DOMAIN}_frontend_module_registered"


def _recording_entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
        return None
    return entry


class NetvueRecordingListView(HomeAssistantView):
    url = "/api/netvue_local/recordings/{entry_id}"
    name = "api:netvue_local:recordings"
    requires_auth = True

    async def post(self, request, entry_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid request"}, status=400)
        enabled = body.get("enabled")
        if type(enabled) is not bool:
            return web.json_response({"error": "invalid request"}, status=400)
        host = entry.runtime_data.camera.host
        session = async_get_clientsession(hass)
        mode = "start" if enabled else "stop"
        try:
            async with session.post(
                f"{API_URL}/cameras/{quote(host, safe='.')}/recording/{mode}",
                timeout=5,
            ) as response:
                data = await response.json()
                if response.status == 200:
                    await entry.runtime_data.async_request_refresh()
                return web.json_response(data, status=response.status)
        except (ClientError, TimeoutError):
            return web.json_response(
                {"error": "recording service unavailable"}, status=502
            )

    async def put(self, request, entry_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid request"}, status=400)
        enabled = body.get("enabled")
        if type(enabled) is not bool:
            return web.json_response({"error": "invalid request"}, status=400)
        host = entry.runtime_data.camera.host
        session = async_get_clientsession(hass)
        try:
            async with session.put(
                f"{API_URL}/cameras/{quote(host, safe='.')}/recording/continuous",
                json={"enabled": enabled},
                timeout=5,
            ) as response:
                data = await response.json()
                if response.status == 200:
                    await entry.runtime_data.async_request_refresh()
                return web.json_response(data, status=response.status)
        except (ClientError, TimeoutError):
            return web.json_response(
                {"error": "recording service unavailable"}, status=502
            )

    async def get(self, request, entry_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        host = entry.runtime_data.camera.host
        params = {}
        if request.query.get("start"):
            params["start"] = request.query["start"]
        if request.query.get("end"):
            params["end"] = request.query["end"]
        session = async_get_clientsession(hass)
        try:
            async with session.get(
                f"{API_URL}/cameras/{quote(host, safe='.')}/recordings",
                params=params,
                timeout=5,
            ) as response:
                data = await response.json()
                return web.json_response(data, status=response.status)
        except (ClientError, TimeoutError):
            return web.json_response(
                {"error": "recording service unavailable"}, status=502
            )


class NetvueRecordingDeleteView(HomeAssistantView):
    url = "/api/netvue_local/recordings/{entry_id}/{recording_id}"
    name = "api:netvue_local:recording_delete"
    requires_auth = True

    async def get(self, request, entry_id, recording_id):
        return await _stream_recording(request, entry_id, recording_id, inline=False)

    async def head(self, request, entry_id, recording_id):
        return await _stream_recording(request, entry_id, recording_id, inline=False)

    async def delete(self, request, entry_id, recording_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        host = entry.runtime_data.camera.host
        session = async_get_clientsession(hass)
        try:
            async with session.delete(
                f"{API_URL}/cameras/{quote(host, safe='.')}/recordings/"
                f"{quote(recording_id, safe='')}",
                timeout=5,
            ) as response:
                data = await response.json()
                return web.json_response(data, status=response.status)
        except (ClientError, TimeoutError):
            return web.json_response(
                {"error": "recording service unavailable"}, status=502
            )


class NetvueRecordingPlaybackView(HomeAssistantView):
    url = "/api/netvue_local/playback/{entry_id}/{recording_id}"
    name = "api:netvue_local:recording_playback"
    requires_auth = True

    async def get(self, request, entry_id, recording_id):
        return await _stream_recording(request, entry_id, recording_id, inline=True)

    async def head(self, request, entry_id, recording_id):
        return await _stream_recording(request, entry_id, recording_id, inline=True)


class NetvueRecordingThumbnailView(HomeAssistantView):
    url = "/api/netvue_local/thumbnail/{entry_id}/{recording_id}"
    name = "api:netvue_local:recording_thumbnail"
    requires_auth = True

    async def get(self, request, entry_id, recording_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        host = entry.runtime_data.camera.host
        try:
            async with async_get_clientsession(hass).get(
                f"{API_URL}/cameras/{quote(host, safe='.')}/recordings/"
                f"{quote(recording_id, safe='')}/thumbnail",
                params={"offset": request.query["offset"]} if "offset" in request.query else None,
                timeout=20,
            ) as response:
                body = await response.read()
                return web.Response(
                    body=body,
                    status=response.status,
                    headers={"Content-Type": response.headers.get("Content-Type", "application/json")},
                )
        except (ClientError, TimeoutError):
            return web.json_response({"error": "recording service unavailable"}, status=502)


async def _stream_recording(request, entry_id, recording_id, *, inline):
    hass = request.app["hass"]
    entry = _recording_entry(hass, entry_id)
    if entry is None:
        return web.json_response({"error": "camera not found"}, status=404)
    host = entry.runtime_data.camera.host
    session = async_get_clientsession(hass)
    try:
        response = await session.request(
            request.method,
            f"{API_URL}/cameras/{quote(host, safe='.')}/recordings/"
            f"{quote(recording_id, safe='')}/download",
            params={"duration": request.query["duration"]} if "duration" in request.query else None,
            headers={
                key: request.headers[key] for key in ("Range", "If-Range")
                if key in request.headers
            },
            timeout=ClientTimeout(total=None, sock_connect=5, sock_read=150),
        )
    except (ClientError, TimeoutError):
        return web.json_response({"error": "recording service unavailable"}, status=502)
    async with response:
        if response.status not in (200, 206):
            return web.Response(
                body=await response.read(),
                status=response.status,
                headers={
                    key: response.headers[key] for key in ("Content-Type", "Content-Range")
                    if key in response.headers
                },
            )
        headers = {
            key: response.headers[key]
            for key in ("Content-Length", "Content-Range", "Accept-Ranges", "ETag")
            if key in response.headers
        }
        headers["Content-Disposition"] = "inline" if inline else response.headers.get(
            "Content-Disposition", f'attachment; filename="netvue-{recording_id}.mp4"'
        )
        headers["Content-Type"] = response.headers.get("Content-Type", "video/mp4")
        output = web.StreamResponse(
            status=response.status,
            headers=headers,
        )
        await output.prepare(request)
        async for chunk in response.content.iter_chunked(64 * 1024):
            await output.write(chunk)
        await output.write_eof()
        return output


async def _async_register_frontend(hass: HomeAssistant, _component: str) -> None:
    if hass.data.get(_FRONTEND_MODULE_REGISTERED_KEY):
        return

    from homeassistant.components import frontend

    version = (await async_get_integration(hass, DOMAIN)).version
    frontend.add_extra_js_url(hass, f"{FRONTEND_URL}/{version}/{CARD_MODULE}")
    hass.data[_FRONTEND_MODULE_REGISTERED_KEY] = True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the Netvue Lovelace card once per Home Assistant instance."""
    hass.http.register_view(NetvueRecordingListView())
    hass.http.register_view(NetvueRecordingDeleteView())
    hass.http.register_view(NetvueRecordingPlaybackView())
    hass.http.register_view(NetvueRecordingThumbnailView())
    if not hass.data.get(_STATIC_PATH_KEY):
        version = (await async_get_integration(hass, DOMAIN)).version
        await hass.http.async_register_static_paths(
            [StaticPathConfig(
                f"{FRONTEND_URL}/{version}",
                str(_FRONTEND_DIR),
                cache_headers=False,
            )]
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
