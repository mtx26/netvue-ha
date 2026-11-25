"""Netvue Local integration for an existing local bridge."""

import asyncio
import logging
import re
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
from homeassistant.helpers.storage import Store

from .bridge_api import (
    API_URL,
    BridgeError,
    async_get_detection_rules,
    async_set_detection_rules,
    async_stop_camera,
    async_set_global_settings,
    async_set_webrtc_config,
    async_get_global_settings,
)
from .const import (
    CONF_AI_RULES,
    CONF_NOTIFY_TARGETS,
    CONF_NOTIFY_COOLDOWN,
    CONF_HOST,
    CONF_STREAM_NAME,
    CONF_STREAM_URL,
    DOMAIN,
    SETTINGS_PANEL_PATH,
    CONF_RETENTION_DAYS,
    CONF_STORAGE_LIMIT_GB,
    CONF_TURN_ENABLED,
    CONF_TURN_KEY_ID,
    CONF_TURN_API_TOKEN,
    CONF_TURN_CLEAR_TOKEN,
    GLOBAL_SETTINGS_DEFAULTS,
    OVERLAY_DEFAULTS,
    OVERLAY_OPTIONS,
    RECORDING_CONTROL_TIMEOUT,
    SETTINGS_STORAGE_KEY,
)
from .coordinator import NetvueCoordinator
from .rtsp import stream_url_from_name
from .zones import configured_zones

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
SETTINGS_PANEL_MODULE = "netvue-settings-panel.js"
_FRONTEND_DIR = Path(__file__).parent / "frontend"
_STATIC_PATH_KEY = f"{DOMAIN}_frontend_static_path_registered"
_FRONTEND_MODULE_REQUESTED_KEY = f"{DOMAIN}_frontend_module_requested"
_FRONTEND_MODULE_REGISTERED_KEY = f"{DOMAIN}_frontend_module_registered"
_RECORDING_KEYS = frozenset((CONF_RETENTION_DAYS, CONF_STORAGE_LIMIT_GB))
_TURN_KEYS = frozenset((CONF_TURN_ENABLED, CONF_TURN_KEY_ID, CONF_TURN_API_TOKEN))
_MANAGED_SECTIONS = "_managed_sections"
_TOKEN_CONFIGURED = "turn_token_configured"


def _retention_days(text: str) -> float:
    units = {"h": 3600, "m": 60, "s": 1, "ms": 0.001,
             "us": 0.000001, "µs": 0.000001, "μs": 0.000001, "ns": 0.000000001}
    parts = list(re.finditer(r"(\d+(?:\.\d+)?)(h|ms|us|µs|μs|ns|m|s)", text))
    if not parts or "".join(match.group() for match in parts) != text:
        raise ValueError("Invalid bridge retention")
    return sum(float(match.group(1)) * units[match.group(2)] for match in parts) / 86400


class NetvueSettings:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = Store(
            hass, 1, SETTINGS_STORAGE_KEY, private=True, atomic_writes=True
        )
        self.lock = asyncio.Lock()
        self.values = dict(GLOBAL_SETTINGS_DEFAULTS)
        self.managed_sections = set()
        self.token_configured = False
        self.loaded = False
        self.configured = False

    async def _load(self) -> None:
        if not self.loaded:
            saved = await self.store.async_load()
            if saved is not None:
                self.values.update({key: saved[key] for key in GLOBAL_SETTINGS_DEFAULTS
                                    if key in saved})
                self.values[CONF_TURN_API_TOKEN] = None
                self.managed_sections = set(saved.get(_MANAGED_SECTIONS, ("recording", "turn")))
                self.token_configured = saved.get(
                    _TOKEN_CONFIGURED, bool(self.values[CONF_TURN_API_TOKEN])
                )
                self.configured = True
            else:
                recording, turn = await async_get_global_settings(self.hass)
                try:
                    if (type(recording["recording_limit_bytes"]) is not int
                            or recording["recording_limit_bytes"] < 0
                            or type(turn["enabled"]) is not bool
                            or not isinstance(turn["key_id"], str)
                            or type(turn["token_configured"]) is not bool):
                        raise ValueError("Invalid bridge settings")
                    self.values.update({
                        CONF_RETENTION_DAYS: _retention_days(recording["recording_retention"]),
                        CONF_STORAGE_LIMIT_GB: recording["recording_limit_bytes"] / 1024**3,
                        CONF_TURN_ENABLED: turn["enabled"],
                        CONF_TURN_KEY_ID: turn["key_id"],
                        CONF_TURN_API_TOKEN: None,
                    })
                    self.token_configured = turn["token_configured"]
                except (KeyError, TypeError, ValueError) as exc:
                    raise BridgeError("Bridge returned invalid settings") from exc
            self.loaded = True

    async def async_public(self) -> dict:
        async with self.lock:
            await self._load()
            return {
                key: value for key, value in self.values.items()
                if key != CONF_TURN_API_TOKEN
            } | {_TOKEN_CONFIGURED: self.token_configured}

    async def _apply(self, values: dict, sections: set[str]) -> None:
        if "recording" in sections:
            await async_set_global_settings(
                self.hass,
                retention_days=values[CONF_RETENTION_DAYS],
                storage_limit_gb=values[CONF_STORAGE_LIMIT_GB],
            )
        if "turn" in sections:
            await async_set_webrtc_config(
                self.hass,
                enabled=values[CONF_TURN_ENABLED],
                key_id=values[CONF_TURN_KEY_ID],
                api_token=values[CONF_TURN_API_TOKEN],
            )

    async def async_restore(self) -> None:
        async with self.lock:
            await self._load()
            if self.configured:
                await self._apply(self.values, self.managed_sections)

    async def async_update(self, changes: dict) -> None:
        async with self.lock:
            await self._load()
            values = {**self.values, **{
                key: value for key, value in changes.items()
                if key in GLOBAL_SETTINGS_DEFAULTS
                and (key != CONF_TURN_API_TOKEN or value)
            }}
            if changes.get(CONF_TURN_CLEAR_TOKEN):
                values[CONF_TURN_API_TOKEN] = ""
            token_configured = self.token_configured
            if isinstance(values[CONF_TURN_API_TOKEN], str):
                token_configured = bool(values[CONF_TURN_API_TOKEN].strip())
            if (
                type(values[CONF_RETENTION_DAYS]) not in (int, float)
                or not 1e-9 / 86400 <= values[CONF_RETENTION_DAYS] <= 106751
                or type(values[CONF_STORAGE_LIMIT_GB]) not in (int, float)
                or not 0 <= values[CONF_STORAGE_LIMIT_GB] <= 1048576
                or type(values[CONF_TURN_ENABLED]) is not bool
                or not isinstance(values[CONF_TURN_KEY_ID], str)
                or not (values[CONF_TURN_API_TOKEN] is None
                        or isinstance(values[CONF_TURN_API_TOKEN], str))
                or len(values[CONF_TURN_KEY_ID]) > 256
                or (values[CONF_TURN_API_TOKEN] is not None
                    and len(values[CONF_TURN_API_TOKEN]) > 4096)
                or (values[CONF_TURN_ENABLED] and (
                    not values[CONF_TURN_KEY_ID].strip()
                    or not token_configured
                ))
            ):
                raise ValueError("Invalid global settings")
            if not changes or values == self.values:
                return
            sections = set()
            if any(values[key] != self.values[key] for key in _RECORDING_KEYS):
                sections.add("recording")
            if any(values[key] != self.values[key] for key in _TURN_KEYS):
                sections.add("turn")
            try:
                await self._apply(values, sections)
            except BridgeError:
                try:
                    await self._apply(self.values, sections)
                except BridgeError:
                    pass
                raise
            managed = self.managed_sections | sections
            await self.store.async_save({
                **{
                    key: value for key, value in values.items()
                    if key != CONF_TURN_API_TOKEN
                },
                _MANAGED_SECTIONS: sorted(managed),
                _TOKEN_CONFIGURED: token_configured,
            })
            self.values = {
                **values,
                CONF_TURN_API_TOKEN: None,
            }
            self.managed_sections = managed
            self.token_configured = token_configured
            self.configured = True


def async_get_settings(hass: HomeAssistant) -> NetvueSettings:
    if SETTINGS_STORAGE_KEY not in hass.data:
        hass.data[SETTINGS_STORAGE_KEY] = NetvueSettings(hass)
    return hass.data[SETTINGS_STORAGE_KEY]


class NetvueOverlayView(HomeAssistantView):
    url = "/api/netvue_local/overlay/{entry_id}"
    name = "api:netvue_local:overlay"
    requires_auth = True

    async def get(self, request, entry_id):
        entry = request.app["hass"].config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return web.json_response({"error": "camera not found"}, status=404)
        return web.json_response({
            key.removeprefix("overlay_"): entry.options.get(
                key, entry.data.get(key, OVERLAY_DEFAULTS[key])
            )
            for key in OVERLAY_OPTIONS
        })


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
        return await self._set_recording(request, entry_id, continuous=False)

    async def put(self, request, entry_id):
        return await self._set_recording(request, entry_id, continuous=True)

    async def _set_recording(self, request, entry_id, *, continuous: bool):
        hass = request.app["hass"]
        entry = _recording_entry(hass, entry_id)
        if entry is None:
            return web.json_response({"error": "camera not found"}, status=404)
        try:
            body = await request.json()
        except ValueError:
            return web.json_response({"error": "invalid request"}, status=400)
        if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
            return web.json_response({"error": "invalid request"}, status=400)
        enabled = body["enabled"]
        host = entry.runtime_data.camera.host
        session = async_get_clientsession(hass)
        mode = "continuous" if continuous else ("start" if enabled else "stop")
        try:
            async with session.request(
                "PUT" if continuous else "POST",
                f"{API_URL}/cameras/{quote(host, safe='.')}/recording/{mode}",
                json={"enabled": enabled} if continuous else None,
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
                timeout=RECORDING_CONTROL_TIMEOUT,
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
                timeout=RECORDING_CONTROL_TIMEOUT,
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
                timeout=RECORDING_CONTROL_TIMEOUT,
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
                timeout=RECORDING_CONTROL_TIMEOUT + 15,
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
            "zones": configured_zones(entry),
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


async def _async_register_frontend(hass: HomeAssistant, _component: str) -> None:
    if hass.data.get(_FRONTEND_MODULE_REGISTERED_KEY):
        return

    from homeassistant.components import frontend, panel_custom

    version = (await async_get_integration(hass, DOMAIN)).version
    frontend.add_extra_js_url(hass, f"{FRONTEND_URL}/{version}/{CARD_MODULE}")
    frontend.add_extra_js_url(hass, f"{FRONTEND_URL}/{version}/{PROTECTED_CARD_MODULE}")
    # Hidden admin page opened by the camera card settings button (no sidebar entry).
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=SETTINGS_PANEL_PATH,
        webcomponent_name="netvue-settings-panel",
        module_url=f"{FRONTEND_URL}/{version}/{SETTINGS_PANEL_MODULE}",
        require_admin=True,
        config_panel_domain=DOMAIN,
    )
    hass.data[_FRONTEND_MODULE_REGISTERED_KEY] = True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the Netvue Lovelace card once per Home Assistant instance."""
    hass.http.register_view(NetvueOverlayView())
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
        await async_get_settings(hass).async_restore()
        await coordinator.camera.async_reconcile()
    except BridgeError as exc:
        raise ConfigEntryNotReady("Netvue Bridge session manager is unavailable") from exc
    await coordinator.async_refresh()
    if coordinator.data is not None and coordinator.data.detection is not None:
        coordinator.live_detection.async_set_updated_data(coordinator.data.detection)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(
        coordinator.live_detection.async_add_listener(coordinator.async_poll_live_events)
    )
    coordinator.async_poll_live_events()
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
