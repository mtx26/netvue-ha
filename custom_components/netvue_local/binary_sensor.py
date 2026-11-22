"""RTSP publisher availability, not camera connection status."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NetvueCoordinator, NetvueEntity, NetvueLiveDetectionEntity
from .zones import configured_zone_names, zone_identity, zones_updated_signal


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([
        NetvueStreamSensor(entry, entry.runtime_data),
        NetvueCameraUnavailableSensor(entry, entry.runtime_data),
        NetvueDetectionSensor(entry, entry.runtime_data),
    ])
    zone_sensors = NetvueZoneSensors(hass, entry, async_add_entities)
    zone_sensors.async_sync()
    entry.async_on_unload(
        async_dispatcher_connect(hass, zones_updated_signal(entry.entry_id), zone_sensors.async_sync)
    )


class NetvueZoneSensors:
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
    ) -> None:
        self._entry = entry
        self._registry = er.async_get(hass)
        self._add_entities = async_add_entities
        self._unique_id_prefix = zone_identity(entry.entry_id, "")
        self._sensors: dict[str, NetvueZoneSensor] = {}

    @callback
    def async_sync(self) -> None:
        zones = configured_zone_names(self._entry)
        added = []
        for zone_id, name in zones.items():
            sensor = self._sensors.get(zone_id)
            if sensor is None:
                sensor = NetvueZoneSensor(self._entry, zone_id, name)
                self._sensors[zone_id] = sensor
                added.append(sensor)
            else:
                sensor.async_rename(name, self._registry)

        prefix = self._unique_id_prefix
        for registered in er.async_entries_for_config_entry(self._registry, self._entry.entry_id):
            if (registered.domain == "binary_sensor" and registered.unique_id.startswith(prefix)
                    and registered.unique_id.removeprefix(prefix) not in zones):
                self._registry.async_remove(registered.entity_id)
        for zone_id in self._sensors.keys() - zones.keys():
            del self._sensors[zone_id]
        if added:
            self._add_entities(added)


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


class NetvueDetectionSensor(NetvueLiveDetectionEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "ai_detection"
    _unrecorded_attributes = frozenset({"detections", "last_frame_at", "metrics"})

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ai_detection"
        self._entry = entry

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    @property
    def is_on(self) -> bool:
        detection = self.coordinator.data
        return bool(detection and detection.enabled and detection.state == "detecting"
                    and detection.detections)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        detection = self.coordinator.data
        if detection is None:
            return {}
        return {
            "state": detection.state,
            "enabled": detection.enabled,
            "detections": detection.detections,
            "zone_names": configured_zone_names(self._entry),
            "last_frame_at": detection.last_frame_at,
            "last_event_at": detection.last_event_at,
            "metrics": detection.metrics or {},
        }


class NetvueZoneSensor(NetvueLiveDetectionEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, entry: ConfigEntry, zone_id: str, name: str) -> None:
        super().__init__(entry.runtime_data)
        self._zone_id = zone_id
        self._attr_unique_id = zone_identity(entry.entry_id, zone_id)
        self.entity_id = f"binary_sensor.netvue_zone_{entry.entry_id.lower()}_{zone_id.replace('-', '_')}"
        self._attr_name = name

    @callback
    def async_rename(self, name: str, registry: er.EntityRegistry) -> None:
        if name == self._attr_name:
            return
        self._attr_name = name
        self.__dict__.pop("name", None)
        if (registered := self.registry_entry) is not None:
            registry.async_update_entity(registered.entity_id, original_name=name)

    @property
    def available(self) -> bool:
        return self.coordinator.zone_detection_available

    @property
    def is_on(self) -> bool:
        return self.coordinator.has_detection_in_zone(self._zone_id)
