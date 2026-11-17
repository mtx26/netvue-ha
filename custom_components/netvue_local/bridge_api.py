"""Loopback API client for the Netvue Bridge session manager."""

import asyncio
from urllib.parse import quote
from weakref import WeakValueDictionary

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .rtsp import stream_url_from_name

API_URL = "http://127.0.0.1:8765/api/v1"
REQUEST_TIMEOUT = 5


class BridgeError(Exception):
    """The local bridge could not accept the requested camera session."""


class _CameraState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.generation = 0


def _camera_state(hass: HomeAssistant, host: str) -> _CameraState:
    states = hass.data.setdefault(f"{DOMAIN}_camera_states", WeakValueDictionary())
    state = states.get(host)
    if state is None:
        state = states[host] = _CameraState()
    return state


async def _camera_response(response, host: str, stream_name: str) -> None:
    try:
        data = await response.json()
    except (ValueError, ClientError) as exc:
        raise BridgeError("Bridge returned invalid camera JSON") from exc
    if (not isinstance(data, dict) or data.get("host") != host
            or data.get("stream_name") != stream_name
            or data.get("rtsp_url") != stream_url_from_name(stream_name)
            or not isinstance(data.get("state"), str) or not data["state"]):
        raise BridgeError("Bridge returned inconsistent camera settings")


class ManagedCamera:
    """Reconcile one HA entry, without restarting an existing manager entry."""

    def __init__(self, hass: HomeAssistant, host: str, stream_name: str) -> None:
        self.hass = hass
        self.host = host
        self.stream_name = stream_name
        self._state = _camera_state(hass, host)
        self._state.generation += 1
        self._generation = self._state.generation

    async def async_reconcile(self) -> None:
        state = self._state
        async with state.lock:
            if self._generation != state.generation:
                return
            session = async_get_clientsession(self.hass)
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    async with session.get(
                        f"{API_URL}/cameras/{quote(self.host, safe='.')}"
                    ) as response:
                        if response.status == 200:
                            await _camera_response(response, self.host, self.stream_name)
                            return
                        if response.status != 404:
                            raise BridgeError(f"Bridge returned HTTP {response.status}")
                    # A newer coordinator may replace this entry while GET is in flight.
                    if self._generation != state.generation:
                        return
                    async with session.post(
                        f"{API_URL}/cameras",
                        json={"host": self.host, "stream_name": self.stream_name},
                    ) as response:
                        if response.status not in (200, 202):
                            raise BridgeError(f"Bridge returned HTTP {response.status}")
                        await _camera_response(response, self.host, self.stream_name)
            except (ClientError, TimeoutError) as exc:
                raise BridgeError("Cannot reach the local bridge manager") from exc


async def async_stop_camera(hass: HomeAssistant, host: str) -> None:
    state = _camera_state(hass, host)
    async with state.lock:
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
        state.generation += 1
