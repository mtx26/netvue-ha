"""RTSP publisher availability, not camera connection status."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NetvueCoordinator, NetvueEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([
        NetvueStreamSensor(entry, entry.runtime_data),
        NetvueCameraUnavailableSensor(entry, entry.runtime_data),
    ])


class NetvueStreamSensor(NetvueEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "rtsp_available"

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_rtsp"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.rtsp_available if self.coordinator.data is not None else None


class NetvueCameraUnavailableSensor(NetvueEntity, BinarySensorEntity):
    """A problem entity suitable for Home Assistant alert automations."""

    _attr_has_entity_name = True
    _attr_translation_key = "camera_unavailable"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_unavailable"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return not data.rtsp_available if data is not None else None
