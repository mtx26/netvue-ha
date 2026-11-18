"""WebRTC signalling through the local manager, not a media proxy."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote

from aiohttp import ClientError
from homeassistant.components.camera import (
    Camera, CameraWebRTCProvider, WebRTCAnswer, WebRTCCandidate, WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from mashumaro import MissingField
from webrtc_models import RTCIceCandidateInit

from . import bridge_api
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_EVENT_TIMEOUT = 35
_MAX_EVENTS_SIZE = 256 * 1024
_MAX_EVENTS = 128


@dataclass
class _Session:
    ready: asyncio.Event
    lock: asyncio.Lock
    task: asyncio.Task | None = None
    started: bool = False
    opened: bool = False
    closing: bool = False


class NetvueWebRTCProvider(CameraWebRTCProvider):
    """Route one camera's HA signalling to the manager."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, camera: Camera, host: str, url: str,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._camera = camera
        self._host = host
        self._url = url
        self._sessions: dict[str, _Session] = {}
        self._stopping = False

    @property
    def domain(self) -> str:
        return DOMAIN

    @callback
    def async_is_supported(self, stream_source: str) -> bool:
        return not self._stopping and stream_source == self._url

    def _session_url(self, session_id: str) -> str:
        return (f"{bridge_api.API_URL}/cameras/{quote(self._host, safe='.')}"
                f"/webrtc/{quote(session_id, safe='')}")

    async def _request(self, method: str, url: str, *, payload=None, timeout=None):
        client = async_get_clientsession(self._hass)
        try:
            async with asyncio.timeout(timeout or bridge_api.REQUEST_TIMEOUT):
                async with client.request(method, url, json=payload) as response:
                    if method == "PUT":
                        expected = (200, 201)
                    elif method in ("POST", "DELETE"):
                        expected = (204,)
                    else:
                        expected = (200,)
                    if response.status not in expected:
                        raise HomeAssistantError(f"WebRTC bridge returned HTTP {response.status}")
                    if method != "GET":
                        return None
                    body = bytearray()
                    async for chunk in response.content.iter_chunked(16384):
                        body.extend(chunk)
                        if len(body) > _MAX_EVENTS_SIZE:
                            raise HomeAssistantError("WebRTC bridge response too large")
                    try:
                        return json.loads(body)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise HomeAssistantError("Invalid WebRTC bridge response") from exc
        except (ClientError, TimeoutError) as exc:
            raise HomeAssistantError("WebRTC bridge unavailable") from exc

    async def async_handle_async_webrtc_offer(
        self, camera: Camera, offer_sdp: str, session_id: str,
        send_message: WebRTCSendMessage,
    ) -> None:
        if self._stopping or camera is not self._camera:
            raise HomeAssistantError("WebRTC camera is not available")
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id)
                or not offer_sdp or len(offer_sdp.encode("utf-8")) > 128 * 1024):
            raise HomeAssistantError("Invalid WebRTC offer or session identifier")
        if self._sessions:
            raise HomeAssistantError("WebRTC camera already has a session")
        state = self._sessions[session_id] = _Session(asyncio.Event(), asyncio.Lock())
        worker = self._run(session_id, state, offer_sdp, send_message)
        try:
            state.task = self._entry.async_create_background_task(
                self._hass, worker, "netvue WebRTC signalling", eager_start=False,
            )
        except BaseException:
            worker.close()
            del self._sessions[session_id]
            raise

    async def _run(self, session_id, state, offer, send_message):
        url = self._session_url(session_id)
        state.started = True
        try:
            if state.closing:
                return
            await self._request("PUT", url, payload={"offer": offer})
            state.opened = True
            state.ready.set()
            after = 0
            while not state.closing:
                data = await self._request(
                    "GET", f"{url}/events?after={after}", timeout=_EVENT_TIMEOUT
                )
                if (not isinstance(data, dict) or not isinstance(data.get("events"), list)
                        or len(data["events"]) > _MAX_EVENTS):
                    raise HomeAssistantError("Invalid WebRTC bridge events")
                for event in data["events"]:
                    if (not isinstance(event, dict) or type(event.get("seq")) is not int
                            or event["seq"] <= after):
                        raise HomeAssistantError("Invalid WebRTC event sequence")
                    after = event["seq"]
                    kind = event.get("type")
                    if kind == "answer" and isinstance(event.get("answer"), str):
                        message = WebRTCAnswer(event["answer"])
                    elif kind == "candidate" and isinstance(event.get("candidate"), dict):
                        try:
                            message = WebRTCCandidate(RTCIceCandidateInit.from_dict(event["candidate"]))
                        except (MissingField, ValueError, TypeError, KeyError) as exc:
                            raise HomeAssistantError("Invalid WebRTC candidate") from exc
                    elif kind == "error" and isinstance(event.get("code"), str):
                        send_message(WebRTCError("netvue_webrtc_failed", "WebRTC negotiation failed"))
                        return
                    else:
                        raise HomeAssistantError("Invalid WebRTC bridge event")
                    send_message(message)
                if not data["events"]:
                    await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        except HomeAssistantError as exc:
            if not state.closing:
                send_message(WebRTCError("netvue_webrtc_failed", str(exc)))
        except Exception:
            _LOGGER.exception("Unexpected WebRTC signalling error")
            if not state.closing:
                send_message(WebRTCError("netvue_webrtc_failed", "WebRTC signalling failed"))
        finally:
            state.closing = True
            state.ready.set()
            try:
                # The manager must tombstone the id even if the PUT timed out.
                async with state.lock:
                    await self._request("DELETE", url)
            except HomeAssistantError as exc:
                _LOGGER.warning("Unable to close WebRTC session: %s", exc)
            finally:
                if self._sessions.get(session_id) is state:
                    del self._sessions[session_id]

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit,
    ) -> None:
        if len(candidate.candidate.encode("utf-8")) > 8192:
            raise HomeAssistantError("WebRTC candidate too large")
        state = self._sessions.get(session_id)
        if state is None:
            raise HomeAssistantError("Unknown WebRTC session")
        await state.ready.wait()
        async with state.lock:
            if state.closing or not state.opened:
                raise HomeAssistantError("WebRTC session is closed")
            try:
                await self._request(
                    "POST", f"{self._session_url(session_id)}/candidates",
                    payload={"candidate": candidate.to_dict()},
                )
            except (HomeAssistantError, asyncio.CancelledError):
                self.async_close_session(session_id)
                raise

    @callback
    def async_close_session(self, session_id: str) -> None:
        state = self._sessions.get(session_id)
        if state is not None and not state.closing:
            state.closing = True
            if state.task is not None and state.started:
                state.task.cancel()

    async def async_unregister_camera(self, camera: Camera) -> None:
        if camera is self._camera:
            await self._close_all()

    async def async_shutdown(self) -> None:
        self._stopping = True
        await self._close_all()

    async def _close_all(self) -> None:
        tasks = []
        for session_id, state in list(self._sessions.items()):
            self.async_close_session(session_id)
            if state.task is not None:
                tasks.append(state.task)
        if tasks:
            waiter = asyncio.gather(*tasks, return_exceptions=True)
            cancelled = False
            while True:
                try:
                    await asyncio.shield(waiter)
                    break
                except asyncio.CancelledError:
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
