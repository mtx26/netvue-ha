"""Camera backed by a separately managed RTSP bridge."""

import asyncio
from io import BytesIO
import logging
from math import hypot

from PIL import Image
from homeassistant.components import camera as camera_component
from homeassistant.components.camera import (
    Camera, CameraEntityFeature, WebRTCSendMessage, async_register_webrtc_provider,
)
from homeassistant.components.camera.webrtc import WebRTCClientConfiguration
from homeassistant.components.stream import CONF_RTSP_TRANSPORT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from webrtc_models import RTCIceCandidateInit

from .const import CONF_HOST, CONF_STREAM_URL
from .coordinator import NetvueCoordinator, NetvueEntity
from .webrtc import NetvueWebRTCProvider, TurnCredentials, async_get_turn_credentials

_BADGE_SIZE = 80
_TURN_REFRESH_INTERVAL = 60
_LOGGER = logging.getLogger(__name__)

# HA 2026.7 keeps this helper private; 2026.11 exposes it without the prefix.
async_get_stream_image = getattr(camera_component, "async_get_stream_image", None)
if async_get_stream_image is None:
    async_get_stream_image = getattr(camera_component, "_async_get_stream_image")


def _badge_image(image: bytes) -> bytes:
    with Image.open(BytesIO(image)) as source:
        # Leave room for JPEG edge artifacts and circular HA badges.
        scale = (_BADGE_SIZE - 6) / hypot(source.width, source.height)
        size = (max(1, round(source.width * scale)),
                max(1, round(source.height * scale)))
        resized = source.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        badge = Image.new("RGB", (_BADGE_SIZE, _BADGE_SIZE), "black")
        badge.paste(resized, ((_BADGE_SIZE - size[0]) // 2, (_BADGE_SIZE - size[1]) // 2))
        output = BytesIO()
        badge.save(output, format="JPEG")
        return output.getvalue()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    try:
        turn = await async_get_turn_credentials(hass)
    except HomeAssistantError as exc:
        _LOGGER.warning("%s; keeping the local stream configuration", exc)
    else:
        if turn is not None:
            async_add_entities([NetvueTurnCamera(entry, entry.runtime_data, turn)])
            return
    async_add_entities([NetvueCamera(entry, entry.runtime_data)])


class NetvueCamera(NetvueEntity, Camera):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        Camera.__init__(self)
        NetvueEntity.__init__(self, coordinator)
        self._attr_unique_id = entry.entry_id
        url = entry.options.get(CONF_STREAM_URL, entry.data[CONF_STREAM_URL])
        self._url = url
        self._entry = entry
        self._host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST))
        self._netvue_provider = None
        self._remove_provider = None
        self.stream_options[CONF_RTSP_TRANSPORT] = "tcp"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._netvue_provider = self._make_webrtc_provider()
        self._remove_provider = self._register_webrtc_provider(self._netvue_provider)

    def _make_webrtc_provider(self) -> NetvueWebRTCProvider:
        return NetvueWebRTCProvider(self.hass, self._entry, self, self._host, self._url)

    def _register_webrtc_provider(self, provider: NetvueWebRTCProvider):
        return async_register_webrtc_provider(self.hass, provider)

    async def async_will_remove_from_hass(self) -> None:
        try:
            if self._remove_provider is not None:
                self._remove_provider()
                self._remove_provider = None
            if self._netvue_provider is not None:
                await self._netvue_provider.async_shutdown()
                self._netvue_provider = None
        finally:
            await super().async_will_remove_from_hass()

    @property
    def available(self) -> bool:
        return (super().available and self.coordinator.data is not None
                and self.coordinator.data.rtsp_available)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        # HA Stream caches its last image and otherwise stretches it to the request size.
        image = await async_get_stream_image(
            self, wait_for_next_keyframe=width is None and height is None
        )
        if image is not None and width == height == _BADGE_SIZE:
            return await self.hass.async_add_executor_job(_badge_image, image)
        return image

    async def stream_source(self) -> str:
        return self._url


class NetvueTurnCamera(NetvueCamera):
    """Use Netvue signalling and private ICE configuration only on this entity."""

    def __init__(
        self, entry: ConfigEntry, coordinator: NetvueCoordinator, turn: TurnCredentials,
    ) -> None:
        super().__init__(entry, coordinator)
        self._turn = turn
        self._turn_refresh: asyncio.Task | None = None

    def _register_webrtc_provider(self, provider: NetvueWebRTCProvider):
        # HA bypasses providers for a camera that supplies native WebRTC.
        return None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if not self._turn.usable():
            _LOGGER.warning("Netvue TURN is unavailable; only direct WebRTC may work")
        self._turn_refresh = self._entry.async_create_background_task(
            self.hass, self._async_refresh_turn(), "netvue TURN credentials",
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._turn_refresh is not None:
            self._turn_refresh.cancel()
            await asyncio.gather(self._turn_refresh, return_exceptions=True)
            self._turn_refresh = None
        await super().async_will_remove_from_hass()

    async def _async_refresh_turn(self) -> None:
        unavailable = not self._turn.usable()
        while True:
            await asyncio.sleep(_TURN_REFRESH_INTERVAL)
            try:
                turn = await async_get_turn_credentials(self.hass)
            except HomeAssistantError:
                turn = None
            if turn is not None and turn.usable():
                self._turn = turn
                if unavailable:
                    _LOGGER.info("Netvue TURN credentials are available again")
                unavailable = False
            elif not unavailable:
                _LOGGER.warning("Unable to refresh Netvue TURN credentials")
                unavailable = True

    @callback
    def _async_get_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
        configuration = WebRTCClientConfiguration()
        configuration.configuration.ice_servers.extend(self._turn.usable())
        return configuration

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage,
    ) -> None:
        if self._netvue_provider is None:
            raise HomeAssistantError("Netvue WebRTC camera is not available")
        await self._netvue_provider.async_handle_async_webrtc_offer(
            self, offer_sdp, session_id, send_message,
        )

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit,
    ) -> None:
        if self._netvue_provider is None:
            raise HomeAssistantError("Netvue WebRTC camera is not available")
        await self._netvue_provider.async_on_webrtc_candidate(session_id, candidate)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        if self._netvue_provider is not None:
            self._netvue_provider.async_close_session(session_id)
