"""Camera backed by a separately managed RTSP bridge."""

from io import BytesIO
from math import hypot

from PIL import Image
from homeassistant.components import camera as camera_component
from homeassistant.components.camera import (
    Camera, CameraEntityFeature, async_register_webrtc_provider,
)
from homeassistant.components.stream import CONF_RTSP_TRANSPORT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_HOST, CONF_STREAM_URL, CONF_TALKBACK_EXPERIMENTAL, CONF_WEBRTC_ENABLED, DOMAIN,
)
from .coordinator import NetvueCoordinator
from .webrtc import NetvueWebRTCProvider

_BADGE_SIZE = 80

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
    async_add_entities([NetvueCamera(entry, entry.runtime_data)])


class NetvueCamera(CoordinatorEntity[NetvueCoordinator], Camera):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title, manufacturer="Netvue"
        )
        url = entry.options.get(CONF_STREAM_URL, entry.data[CONF_STREAM_URL])
        self._url = url
        self._entry = entry
        self._host = entry.options.get(CONF_HOST, entry.data.get(CONF_HOST))
        self._webrtc_enabled = entry.options.get(CONF_WEBRTC_ENABLED, False)
        self._talkback_enabled = (
            self._webrtc_enabled and entry.options.get(CONF_TALKBACK_EXPERIMENTAL, False)
        )
        capabilities_type = getattr(camera_component, "CameraEntityCapabilities", None)
        if capabilities_type is not None:
            self._attr_camera_entity_capabilities = capabilities_type(
                two_way_audio=self._talkback_enabled
            )
        self._netvue_provider = None
        self._remove_provider = None
        self.stream_options[CONF_RTSP_TRANSPORT] = "tcp"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._webrtc_enabled:
            provider = NetvueWebRTCProvider(
                self.hass, self._entry, self, self._host, self._url,
                allow_microphone=self._talkback_enabled,
            )
            self._remove_provider = async_register_webrtc_provider(self.hass, provider)
            self._netvue_provider = provider

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
