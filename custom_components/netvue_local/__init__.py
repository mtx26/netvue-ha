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

from .bridge_api import (
    API_URL,
    BridgeError,
    async_set_detection_config,
    async_get_detection_rules,
    async_set_detection_rules,
    async_get_detection_status,
    async_stop_camera,
)
from .const import (
    CONF_AI_CONFIDENCE,
    CONF_AI_CONFIRMATION_FRAMES,
    CONF_AI_ENABLED,
    CONF_AI_FPS,
    CONF_AI_ZONES,
    CONF_AI_RULES,
    CONF_NOTIFY_TARGETS,
    CONF_NOTIFY_COOLDOWN,
    CONF_HOST,
    CONF_STREAM_NAME,
    CONF_STREAM_URL,
    DEFAULT_AI_CONFIDENCE,
    DOMAIN,
)
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
PROTECTED_CARD_MODULE = "netvue-protected-card.js"
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


class NetvueRecordingMetadataView(HomeAssistantView):
    url = "/api/netvue_local/recordings/{entry_id}/{recording_id}/metadata"
    name = "api:netvue_local:recording_metadata"
    requires_auth = True

    async def get(self, request, entry_id, recording_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        host = entry.runtime_data.camera.host
        session = async_get_clientsession(hass)
        try:
            async with session.get(
                f"{API_URL}/cameras/{quote(host, safe='.')}/recordings/"
                f"{quote(recording_id, safe='')}/metadata",
                timeout=5,
            ) as response:
                data = await response.json()
                return web.json_response(data, status=response.status)
        except (ClientError, TimeoutError, ValueError):
            return web.json_response(
                {"error": "recording metadata unavailable"}, status=502
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


class NetvueProtectedRecordingListView(HomeAssistantView):
    url = "/api/netvue_local/protected/{entry_id}"
    name = "api:netvue_local:protected"
    requires_auth = True

    async def get(self, request, entry_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        host = entry.runtime_data.camera.host
        try:
            async with async_get_clientsession(hass).get(
                f"{API_URL}/cameras/{quote(host, safe='.')}/protected",
                timeout=10,
            ) as response:
                data = await response.json()
                return web.json_response(data, status=response.status)
        except (ClientError, TimeoutError, ValueError):
            return web.json_response({"error": "protected recordings unavailable"}, status=502)

    async def post(self, request, entry_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid request"}, status=400)
        host = entry.runtime_data.camera.host
        try:
            async with async_get_clientsession(hass).post(
                f"{API_URL}/cameras/{quote(host, safe='.')}/protected",
                json=body,
                timeout=120,
            ) as response:
                data = await response.json()
                return web.json_response(data, status=response.status)
        except (ClientError, TimeoutError, ValueError):
            return web.json_response({"error": "protected recording unavailable"}, status=502)


class NetvueProtectedStorageHealthView(HomeAssistantView):
    url = "/api/netvue_local/protected-health"
    name = "api:netvue_local:protected_health"
    requires_auth = True

    async def get(self, request):
        hass = request.app["hass"]
        try:
            async with async_get_clientsession(hass).get(
                f"{API_URL}/recordings/protected/health", timeout=5
            ) as response:
                data = await response.json()
                return web.json_response(data, status=response.status)
        except (ClientError, TimeoutError, ValueError):
            return web.json_response({"error": "storage health unavailable"}, status=502)


class NetvueEventHistoryView(HomeAssistantView):
    url = "/api/netvue_local/events/{entry_id}"
    name = "api:netvue_local:event_history"
    requires_auth = True

    async def get(self, request, entry_id):
        entry = _recording_entry(request.app["hass"], entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        return web.json_response({"events": entry.runtime_data.event_history})


class NetvueProtectedRecordingView(HomeAssistantView):
    url = "/api/netvue_local/protected/{entry_id}/{recording_id}"
    name = "api:netvue_local:protected_recording"
    requires_auth = True

    async def get(self, request, entry_id, recording_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        host = entry.runtime_data.camera.host
        try:
            response = await async_get_clientsession(hass).request(
                request.method,
                f"{API_URL}/cameras/{quote(host, safe='.')}/protected/"
                f"{quote(recording_id, safe='')}/download",
                headers={key: request.headers[key] for key in ("Range", "If-Range") if key in request.headers},
                timeout=ClientTimeout(total=None, sock_connect=5, sock_read=150),
            )
        except (ClientError, TimeoutError):
            return web.json_response({"error": "protected recording unavailable"}, status=502)
        async with response:
            if response.status not in (200, 206):
                headers = {
                    key: response.headers[key]
                    for key in ("Content-Range", "Accept-Ranges")
                    if key in response.headers
                }
                return web.Response(
                    body=await response.read(), status=response.status, headers=headers
                )
            headers = {
                key: response.headers[key]
                for key in (
                    "Content-Length", "Content-Range", "Accept-Ranges", "ETag",
                    "Last-Modified",
                )
                if key in response.headers
            }
            headers["Content-Type"] = response.headers.get("Content-Type", "video/mp4")
            disposition = "attachment" if request.query.get("download") == "1" else "inline"
            headers["Content-Disposition"] = disposition
            output = web.StreamResponse(status=response.status, headers=headers)
            await output.prepare(request)
            if request.method != "HEAD":
                async for chunk in response.content.iter_chunked(64 * 1024):
                    await output.write(chunk)
            await output.write_eof()
            return output

    async def head(self, request, entry_id, recording_id):
        return await self.get(request, entry_id, recording_id)

    async def delete(self, request, entry_id, recording_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        host = entry.runtime_data.camera.host
        try:
            async with async_get_clientsession(hass).delete(
                f"{API_URL}/cameras/{quote(host, safe='.')}/protected/"
                f"{quote(recording_id, safe='')}",
                timeout=10,
            ) as response:
                data = await response.json()
                return web.json_response(data, status=response.status)
        except (ClientError, TimeoutError, ValueError):
            return web.json_response({"error": "protected recording unavailable"}, status=502)


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
            params={
                key: request.query[key] for key in ("duration", "overlay")
                if key in request.query
            },
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
                    key: response.headers[key] for key in ("Content-Type", "Content-Range", "Retry-After")
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


class NetvueDetectionRulesView(HomeAssistantView):
    url = "/api/netvue_local/rules/{entry_id}"
    name = "api:netvue_local:detection_rules"
    requires_auth = True

    async def get(self, request, entry_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        stream_name = entry.options.get(CONF_STREAM_NAME, entry.data[CONF_STREAM_NAME])
        try:
            rules = await async_get_detection_rules(hass, stream_name)
        except BridgeError as exc:
            return web.json_response({"error": str(exc)}, status=502)
        return web.json_response({
            "rules": rules,
            "notification_targets": entry.options.get(
                CONF_NOTIFY_TARGETS, entry.data.get(CONF_NOTIFY_TARGETS, [])
            ),
            "notification_cooldown": entry.options.get(
                CONF_NOTIFY_COOLDOWN, entry.data.get(CONF_NOTIFY_COOLDOWN, 30)
            ),
        })

    async def put(self, request, entry_id):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid request"}, status=400)
        rules = body.get("rules")
        targets = body.get("notification_targets")
        cooldown = body.get("notification_cooldown")
        if not isinstance(rules, list):
            return web.json_response({"error": "invalid rules"}, status=400)
        if not isinstance(targets, list) or not all(isinstance(target, str) for target in targets):
            return web.json_response({"error": "invalid notification targets"}, status=400)
        if type(cooldown) is not int or cooldown < 0 or cooldown > 86400:
            return web.json_response({"error": "invalid notification cooldown"}, status=400)
        stream_name = entry.options.get(CONF_STREAM_NAME, entry.data[CONF_STREAM_NAME])
        try:
            rules = await async_set_detection_rules(hass, stream_name, rules)
        except BridgeError as exc:
            return web.json_response({"error": str(exc)}, status=502)
        options = dict(entry.options)
        options[CONF_AI_RULES] = rules
        options[CONF_NOTIFY_TARGETS] = targets
        options[CONF_NOTIFY_COOLDOWN] = cooldown
        hass.config_entries.async_update_entry(entry, options=options)
        return web.json_response({
            "rules": rules,
            "notification_targets": targets,
            "notification_cooldown": cooldown,
        })


class NetvueDetectionConfigView(HomeAssistantView):
    url = "/api/netvue_local/detection/{entry_id}"
    name = "api:netvue_local:detection"
    requires_auth = True

    @staticmethod
    def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return None
        return entry

    async def get(self, request, entry_id):
        hass = request.app["hass"]
        entry = self._entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        stream_name = entry.options.get(
            CONF_STREAM_NAME, entry.data[CONF_STREAM_NAME]
        )
        config = {
            CONF_AI_ENABLED: entry.options.get(
                CONF_AI_ENABLED, entry.data.get(CONF_AI_ENABLED, True)
            ),
            CONF_AI_CONFIDENCE: entry.options.get(
                CONF_AI_CONFIDENCE, entry.data.get(CONF_AI_CONFIDENCE, DEFAULT_AI_CONFIDENCE)
            ),
            CONF_AI_FPS: entry.options.get(
                CONF_AI_FPS, entry.data.get(CONF_AI_FPS, 3)
            ),
            CONF_AI_CONFIRMATION_FRAMES:
                entry.options.get(
                    CONF_AI_CONFIRMATION_FRAMES,
                    entry.data.get(CONF_AI_CONFIRMATION_FRAMES, 3),
                ),
            CONF_AI_ZONES: entry.options.get(
                CONF_AI_ZONES, entry.data.get(CONF_AI_ZONES, {})
            ),
        }
        try:
            status = await async_get_detection_status(hass, stream_name)
        except BridgeError:
            status = None
        if status is not None:
            config[CONF_AI_ENABLED] = status.enabled
        return web.json_response(config)

    async def put(self, request, entry_id):
        hass = request.app["hass"]
        entry = self._entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid request"}, status=400)
        enabled = body.get(CONF_AI_ENABLED)
        confidence = body.get(CONF_AI_CONFIDENCE)
        fps = body.get(CONF_AI_FPS)
        confirmation = body.get(CONF_AI_CONFIRMATION_FRAMES)
        zones = body.get(CONF_AI_ZONES)
        if (type(enabled) is not bool
                or type(confidence) not in (int, float)
                or not 0.1 <= confidence <= 0.95
                or type(fps) not in (int, float)
                or not 1 <= fps <= 10
                or type(confirmation) is not int
                or not 1 <= confirmation <= 10
                or not isinstance(zones, dict)):
            return web.json_response({"error": "invalid request"}, status=400)
        for name, rect in zones.items():
            if (name not in ("entrance", "doorstep", "garden", "driveway")
                    or not isinstance(rect, list)
                    or len(rect) != 4
                    or not all(type(value) in (int, float)
                               and 0 <= value <= 1 for value in rect)):
                return web.json_response({"error": "invalid request"}, status=400)
        stream_name = entry.options.get(
            CONF_STREAM_NAME, entry.data[CONF_STREAM_NAME]
        )
        try:
            await async_set_detection_config(
                hass,
                stream_name,
                enabled=enabled,
                confidence=float(confidence),
                fps=float(fps),
                confirmation_frames=confirmation,
                zones=zones,
            )
        except BridgeError as exc:
            return web.json_response({"error": str(exc)}, status=502)
        options = dict(entry.options)
        options.update({
            CONF_AI_ENABLED: enabled,
            CONF_AI_CONFIDENCE: float(confidence),
            CONF_AI_FPS: float(fps),
            CONF_AI_CONFIRMATION_FRAMES: confirmation,
            CONF_AI_ZONES: zones,
        })
        hass.config_entries.async_update_entry(entry, options=options)
        return web.json_response({
            CONF_AI_ENABLED: enabled,
            CONF_AI_CONFIDENCE: float(confidence),
            CONF_AI_FPS: float(fps),
            CONF_AI_CONFIRMATION_FRAMES: confirmation,
            CONF_AI_ZONES: zones,
        })


async def _async_register_frontend(hass: HomeAssistant, _component: str) -> None:
    if hass.data.get(_FRONTEND_MODULE_REGISTERED_KEY):
        return

    from homeassistant.components import frontend

    version = (await async_get_integration(hass, DOMAIN)).version
    frontend.add_extra_js_url(hass, f"{FRONTEND_URL}/{version}/{CARD_MODULE}")
    frontend.add_extra_js_url(hass, f"{FRONTEND_URL}/{version}/{PROTECTED_CARD_MODULE}")
    hass.data[_FRONTEND_MODULE_REGISTERED_KEY] = True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the Netvue Lovelace card once per Home Assistant instance."""
    hass.http.register_view(NetvueDetectionConfigView())
    hass.http.register_view(NetvueDetectionRulesView())
    hass.http.register_view(NetvueRecordingListView())
    hass.http.register_view(NetvueRecordingDeleteView())
    hass.http.register_view(NetvueRecordingMetadataView())
    hass.http.register_view(NetvueRecordingPlaybackView())
    hass.http.register_view(NetvueRecordingThumbnailView())
    hass.http.register_view(NetvueProtectedRecordingListView())
    hass.http.register_view(NetvueProtectedStorageHealthView())
    hass.http.register_view(NetvueEventHistoryView())
    hass.http.register_view(NetvueProtectedRecordingView())
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
    await coordinator.async_load_event_history()
    try:
        await coordinator.camera.async_reconcile()
    except BridgeError as exc:
        raise ConfigEntryNotReady("Netvue Bridge session manager is unavailable") from exc
    await coordinator.async_refresh()
    if coordinator.data is not None and coordinator.data.detection is not None:
        coordinator.live_detection.async_set_updated_data(coordinator.data.detection)
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
