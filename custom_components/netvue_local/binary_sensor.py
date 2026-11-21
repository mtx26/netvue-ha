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
        NetvueDetectionSensor(entry, entry.runtime_data),
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


class NetvueDetectionSensor(NetvueEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "ai_detection"

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ai_detection"

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and data.detection is not None

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        if not data or not data.detection:
            return False
        return data.detection.enabled and bool(data.detection.detections)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data
        if not data or not data.detection:
            return {}
        return {
            "state": data.detection.state,
            "enabled": data.detection.enabled,
            "detections": data.detection.detections,
            "last_frame_at": data.detection.last_frame_at,
            "last_event_at": data.detection.last_event_at,
        }
