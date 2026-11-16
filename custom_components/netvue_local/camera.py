"""Camera backed by a separately managed RTSP bridge."""

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.stream import CONF_RTSP_TRANSPORT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_STREAM_URL, DOMAIN
from .coordinator import NetvueCoordinator


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
        self.stream_options[CONF_RTSP_TRANSPORT] = "tcp"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is True

    @property
    def use_stream_for_stills(self) -> bool:
        return True

    async def stream_source(self) -> str:
        return self._url
