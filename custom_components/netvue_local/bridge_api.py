"""Loopback API client for the Netvue Bridge session manager."""

import asyncio
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


class SessionStatus(NamedTuple):
    state: str
    error: str | None


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
    return SessionStatus(data["state"], data.get("error"))


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
