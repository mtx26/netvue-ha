"""Loopback API client for the Netvue Bridge session manager."""

import asyncio
from urllib.parse import quote

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

API_URL = "http://127.0.0.1:8765/api/v1"
REQUEST_TIMEOUT = 5


class BridgeError(Exception):
    """The local bridge could not accept the requested camera session."""


async def async_ensure_camera(hass: HomeAssistant, host: str, stream_name: str) -> None:
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with session.post(
                f"{API_URL}/cameras", json={"host": host, "stream_name": stream_name}
            ) as response:
                if response.status not in (200, 202):
                    raise BridgeError(f"Bridge returned HTTP {response.status}")
    except (ClientError, TimeoutError) as exc:
        raise BridgeError("Cannot reach the local bridge manager") from exc


async def async_stop_camera(hass: HomeAssistant, host: str) -> None:
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with session.delete(f"{API_URL}/cameras/{quote(host, safe='.')}"
                                      ) as response:
                if response.status not in (200, 404):
                    raise BridgeError(f"Bridge returned HTTP {response.status}")
    except (ClientError, TimeoutError) as exc:
        raise BridgeError("Cannot reach the local bridge manager") from exc
