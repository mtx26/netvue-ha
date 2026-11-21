"""Loopback API client for the Netvue Bridge session manager."""

import asyncio
from dataclasses import dataclass, field
from typing import NamedTuple
from urllib.parse import quote
from weakref import WeakValueDictionary

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .rtsp import stream_url_from_name, validate_host

API_URL = "http://127.0.0.1:8765/api/v1"
REQUEST_TIMEOUT = 5
DISCOVERY_TIMEOUT = 30


class BridgeError(Exception):
    """The local bridge could not accept the requested camera session."""


@dataclass(frozen=True)
class CameraMetadata:
    """Optional descriptive data, never used as camera identity or logged."""

    serial_number: str | None = None
    firmware_version: str | None = None
    capabilities: dict[str, bool] = field(default_factory=dict)
    wifi_ssid: str | None = None
    wifi_signal_level: int | None = None


class SessionStatus(NamedTuple):
    state: str
    error: str | None
    metadata: CameraMetadata | None = None
    connection_port: int | None = None


class RecordingStatus(NamedTuple):
    manual: bool
    continuous: bool
    active: bool
    error: str | None = None


class DetectionStatus(NamedTuple):
    enabled: bool
    state: str
    detections: list[dict]
    last_frame_at: float | None
    last_event_at: float | None


AI_API_URL = "http://127.0.0.1:8766/api/v1"


def _detection_status(data) -> DetectionStatus:
    if not isinstance(data, dict):
        raise BridgeError("AI service returned invalid detection status")
    if type(data.get("enabled")) is not bool:
        raise BridgeError("AI service returned invalid detection status")
    if not isinstance(data.get("state"), str):
        raise BridgeError("AI service returned invalid detection status")
    detections = data.get("detections")
    if not isinstance(detections, list):
        raise BridgeError("AI service returned invalid detection status")
    if not all(isinstance(item, dict) for item in detections):
        raise BridgeError("AI service returned invalid detection status")
    last_frame = data.get("last_frame_at")
    last_event = data.get("last_event_at")
    return DetectionStatus(
        data["enabled"],
        data["state"],
        detections,
        last_frame if type(last_frame) in (int, float) else None,
        last_event if type(last_event) in (int, float) else None,
    )


async def async_get_detection_status(hass: HomeAssistant, stream_name: str) -> DetectionStatus:
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with session.get(
                f"{AI_API_URL}/cameras/{quote(stream_name, safe='_')}/status"
            ) as response:
                if response.status == 404:
                    return DetectionStatus(False, "not_configured", [], None, None)
                if response.status != 200:
                    raise BridgeError(f"AI service returned HTTP {response.status}")
                try:
                    data = await response.json()
                except (ValueError, ClientError) as exc:
                    raise BridgeError("AI service returned invalid detection JSON") from exc
                return _detection_status(data)
    except (ClientError, TimeoutError) as exc:
        raise BridgeError("Cannot reach the local AI service") from exc


async def async_set_detection_enabled(
    hass: HomeAssistant, stream_name: str, enabled: bool
) -> DetectionStatus:
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with session.put(
                f"{AI_API_URL}/cameras/{quote(stream_name, safe='_')}/enabled",
                json={"enabled": enabled},
            ) as response:
                if response.status not in (200, 404):
                    raise BridgeError(f"AI service returned HTTP {response.status}")
                try:
                    data = await response.json()
                except (ValueError, ClientError) as exc:
                    raise BridgeError("AI service returned invalid detection JSON") from exc
                if response.status == 404:
                    raise BridgeError("AI service has no matching camera")
                return _detection_status(data)
    except (ClientError, TimeoutError) as exc:
        raise BridgeError("Cannot reach the local AI service") from exc


async def async_set_detection_config(
    hass: HomeAssistant,
    stream_name: str,
    *,
    enabled: bool,
    confidence: float,
    fps: float,
    confirmation_frames: int,
    zones: dict[str, list[float]],
) -> DetectionStatus:
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with session.put(
                f"{AI_API_URL}/cameras/{quote(stream_name, safe='_')}/config",
                json={
                    "enabled": enabled,
                    "confidence": confidence,
                    "fps": fps,
                    "confirmation_frames": confirmation_frames,
                    "zones": zones,
                },
            ) as response:
                if response.status not in (200, 404):
                    raise BridgeError(f"AI service returned HTTP {response.status}")
                try:
                    data = await response.json()
                except (ValueError, ClientError) as exc:
                    raise BridgeError("AI service returned invalid detection JSON") from exc
                if response.status == 404:
                    raise BridgeError("AI service has no matching camera")
                return _detection_status(data)
    except (ClientError, TimeoutError) as exc:
        raise BridgeError("Cannot reach the local AI service") from exc


async def async_get_detection_events(
    hass: HomeAssistant, stream_name: str, since: str | None = None
) -> list[dict]:
    session = async_get_clientsession(hass)
    params = {"since": since} if since else None
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with session.get(
                f"{AI_API_URL}/cameras/{quote(stream_name, safe='_')}/events",
                params=params,
            ) as response:
                if response.status == 404:
                    return []
                if response.status != 200:
                    raise BridgeError(f"AI service returned HTTP {response.status}")
                data = await response.json()
                events = data.get("events") if isinstance(data, dict) else None
                return events if isinstance(events, list) else []
    except (ClientError, TimeoutError) as exc:
        raise BridgeError("Cannot reach the local AI service") from exc


def _recording_status(data) -> RecordingStatus:
    if not isinstance(data, dict):
        raise BridgeError("Bridge returned invalid recording status")
    if type(data.get("manual")) is not bool or type(data.get("continuous")) is not bool:
        raise BridgeError("Bridge returned invalid recording status")
    if type(data.get("active")) is not bool:
        raise BridgeError("Bridge returned invalid recording status")
    error = data.get("error")
    if error is not None and not isinstance(error, str):
        raise BridgeError("Bridge returned invalid recording status")
    return RecordingStatus(data["manual"], data["continuous"], data["active"], error)


def _camera_metadata(data) -> CameraMetadata | None:
    """Ignore unknown/invalid optional fields without rejecting an older bridge."""
    if not isinstance(data, dict):
        return None
    serial = data.get("serial_number")
    firmware = data.get("firmware_version")
    ssid = data.get("wifi_ssid")
    signal = data.get("wifi_signal_level")
    capabilities = data.get("capabilities")
    return CameraMetadata(
        serial_number=serial if isinstance(serial, str) and serial else None,
        firmware_version=firmware if isinstance(firmware, str) and firmware else None,
        capabilities={
            key: capabilities[key] for key in ("audio", "flip", "light", "sd_card")
            if isinstance(capabilities, dict) and type(capabilities.get(key)) is bool
        },
        wifi_ssid=ssid if isinstance(ssid, str) else None,
        wifi_signal_level=signal if type(signal) is int else None,
    )


async def async_discover_cameras(hass: HomeAssistant) -> list[str]:
    """Request a discovery scan from the bridge, without starting sessions."""
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(DISCOVERY_TIMEOUT):
            async with session.post(f"{API_URL}/discovery") as response:
                if response.status != 200:
                    raise BridgeError(f"Bridge returned HTTP {response.status}")
                try:
                    data = await response.json()
                except (ValueError, ClientError) as exc:
                    raise BridgeError("Bridge returned invalid discovery JSON") from exc
    except (ClientError, TimeoutError) as exc:
        raise BridgeError("Cannot reach the local bridge manager") from exc

    if not isinstance(data, dict) or not isinstance(data.get("cameras"), list):
        raise BridgeError("Bridge returned invalid discovery results")
    hosts = []
    seen = set()
    for camera in data["cameras"]:
        if not isinstance(camera, dict) or not isinstance(camera.get("host"), str):
            raise BridgeError("Bridge returned invalid discovery results")
        try:
            host = validate_host(camera["host"])
        except ValueError as exc:
            raise BridgeError("Bridge returned invalid discovery results") from exc
        if host not in seen:
            seen.add(host)
            hosts.append(host)
    return hosts


class _CameraState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.generation = 0
        self.delete_uncertain = False
        self.add_uncertain = False


def _camera_state(hass: HomeAssistant, host: str) -> _CameraState:
    states = hass.data.setdefault(f"{DOMAIN}_camera_states", WeakValueDictionary())
    state = states.get(host)
    if state is None:
        state = states[host] = _CameraState()
    return state


def _retain_uncertain_state(hass: HomeAssistant, host: str, state: _CameraState) -> None:
    uncertain = hass.data.setdefault(f"{DOMAIN}_uncertain_camera_states", {})
    if state.add_uncertain or state.delete_uncertain:
        uncertain[host] = state
    else:
        uncertain.pop(host, None)


async def _camera_response(response, host: str, stream_name: str) -> SessionStatus:
    try:
        data = await response.json()
    except (ValueError, ClientError) as exc:
        raise BridgeError("Bridge returned invalid camera JSON") from exc
    if (not isinstance(data, dict) or data.get("host") != host
            or data.get("stream_name") != stream_name
            or data.get("rtsp_url") != stream_url_from_name(stream_name)
            or data.get("state") not in ("supervising", "stopping", "stopped", "failed")
            or ("error" in data and not isinstance(data["error"], str))):
        raise BridgeError("Bridge returned inconsistent camera settings")
    port = data.get("connection_port")
    return SessionStatus(
        data["state"], data.get("error"), _camera_metadata(data.get("metadata")),
        port if type(port) is int and 1 <= port <= 65535 else None,
    )


class ManagedCamera:
    """Reconcile one HA entry, without restarting an existing manager entry."""

    def __init__(self, hass: HomeAssistant, host: str, stream_name: str) -> None:
        self.hass = hass
        self.host = host
        self.stream_name = stream_name
        self._state = _camera_state(hass, host)
        self._state.generation += 1
        self._generation = self._state.generation

    async def async_reconcile(self) -> SessionStatus | None:
        state = self._state
        async with state.lock:
            if self._generation != state.generation:
                return
            if state.add_uncertain:
                raise BridgeError("Camera creation outcome is uncertain; manual recovery required")
            session = async_get_clientsession(self.hass)
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    async with session.get(
                        f"{API_URL}/cameras/{quote(self.host, safe='.')}"
                    ) as response:
                        if response.status == 200:
                            return await _camera_response(response, self.host, self.stream_name)
                        if response.status != 404:
                            raise BridgeError(f"Bridge returned HTTP {response.status}")
                    if state.delete_uncertain:
                        raise BridgeError("Camera deletion outcome is uncertain; retry removal")
                    # A newer coordinator may replace this entry while GET is in flight.
                    if self._generation != state.generation:
                        return
                    state.add_uncertain = True
                    _retain_uncertain_state(self.hass, self.host, state)
                    async with session.post(
                        f"{API_URL}/cameras",
                        json={"host": self.host, "stream_name": self.stream_name},
                    ) as response:
                        if response.status not in (200, 202):
                            if response.status in (400, 409, 503):
                                # These responses are issued before insertion.
                                state.add_uncertain = False
                                _retain_uncertain_state(self.hass, self.host, state)
                            raise BridgeError(f"Bridge returned HTTP {response.status}")
                        result = await _camera_response(response, self.host, self.stream_name)
                        state.add_uncertain = False
                        _retain_uncertain_state(self.hass, self.host, state)
                        return result
            except (ClientError, TimeoutError) as exc:
                raise BridgeError("Cannot reach the local bridge manager") from exc

    async def async_start(self) -> SessionStatus:
        state = self._state
        async with state.lock:
            if self._generation != state.generation:
                raise BridgeError("Camera entry has been removed or replaced")
            if state.add_uncertain or state.delete_uncertain:
                raise BridgeError("Camera operation outcome is uncertain; manual recovery required")
            session = async_get_clientsession(self.hass)
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    async with session.post(
                        f"{API_URL}/cameras/{quote(self.host, safe='.')}/start"
                    ) as response:
                        if response.status not in (200, 202):
                            raise BridgeError(f"Bridge returned HTTP {response.status}")
                        return await _camera_response(response, self.host, self.stream_name)
            except (ClientError, TimeoutError) as exc:
                raise BridgeError("Cannot reach the local bridge manager") from exc

    async def async_recording_status(self) -> RecordingStatus:
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with session.get(
                    f"{API_URL}/cameras/{quote(self.host, safe='.')}/recording"
                ) as response:
                    if response.status == 404:
                        return RecordingStatus(False, False, False)
                    if response.status != 200:
                        raise BridgeError(f"Bridge returned HTTP {response.status}")
                    try:
                        data = await response.json()
                    except (ValueError, ClientError) as exc:
                        raise BridgeError("Bridge returned invalid recording JSON") from exc
                    return _recording_status(data)
        except (ClientError, TimeoutError) as exc:
            raise BridgeError("Cannot reach the local bridge manager") from exc

    async def async_set_recording(self, enabled: bool) -> RecordingStatus:
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with session.post(
                    f"{API_URL}/cameras/{quote(self.host, safe='.')}/recording/"
                    + ("start" if enabled else "stop")
                ) as response:
                    if response.status != 200:
                        raise BridgeError(f"Bridge returned HTTP {response.status}")
                    try:
                        data = await response.json()
                    except (ValueError, ClientError) as exc:
                        raise BridgeError("Bridge returned invalid recording JSON") from exc
                    return _recording_status(data)
        except (ClientError, TimeoutError) as exc:
            raise BridgeError("Cannot reach the local bridge manager") from exc

    async def async_set_continuous_recording(self, enabled: bool) -> RecordingStatus:
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with session.put(
                    f"{API_URL}/cameras/{quote(self.host, safe='.')}/recording/continuous",
                    json={"enabled": enabled},
                ) as response:
                    if response.status != 200:
                        raise BridgeError(f"Bridge returned HTTP {response.status}")
                    try:
                        data = await response.json()
                    except (ValueError, ClientError) as exc:
                        raise BridgeError("Bridge returned invalid recording JSON") from exc
                    return _recording_status(data)
        except (ClientError, TimeoutError) as exc:
            raise BridgeError("Cannot reach the local bridge manager") from exc


async def async_stop_camera(hass: HomeAssistant, host: str) -> None:
    state = _camera_state(hass, host)
    async with state.lock:
        if state.add_uncertain:
            raise BridgeError("Camera creation outcome is uncertain; manual recovery required")
        state.delete_uncertain = True
        _retain_uncertain_state(hass, host, state)
        session = async_get_clientsession(hass)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with session.delete(
                    f"{API_URL}/cameras/{quote(host, safe='.')}"
                ) as response:
                    if response.status not in (200, 404):
                        raise BridgeError(f"Bridge returned HTTP {response.status}")
        except (ClientError, TimeoutError) as exc:
            raise BridgeError("Cannot reach the local bridge manager") from exc
        state.delete_uncertain = False
        _retain_uncertain_state(hass, host, state)
        state.generation += 1
