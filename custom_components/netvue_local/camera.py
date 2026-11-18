"""Camera backed by a separately managed RTSP bridge."""

from homeassistant.components.camera import (
    Camera, CameraEntityCapabilities, CameraEntityFeature, async_register_webrtc_provider,
)
from homeassistant.components.stream import CONF_RTSP_TRANSPORT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, CONF_STREAM_URL, CONF_WEBRTC_ENABLED, DOMAIN
from .coordinator import NetvueCoordinator
from .webrtc import NetvueWebRTCProvider


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([NetvueCamera(entry, entry.runtime_data)])


class NetvueCamera(CoordinatorEntity[NetvueCoordinator], Camera):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM
    # Keep HA's microphone control hidden until speaker playback is verified.
    _attr_camera_entity_capabilities = CameraEntityCapabilities(two_way_audio=False)

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
        self._netvue_provider = None
        self._remove_provider = None
        self.stream_options[CONF_RTSP_TRANSPORT] = "tcp"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._webrtc_enabled:
            provider = NetvueWebRTCProvider(self.hass, self._entry, self, self._host, self._url)
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

    @property
    def use_stream_for_stills(self) -> bool:
        return True

    async def stream_source(self) -> str:
        return self._url
