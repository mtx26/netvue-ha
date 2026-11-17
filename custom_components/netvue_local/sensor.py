"""Session status reported by the local Go bridge."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NetvueCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([NetvueSessionSensor(entry, entry.runtime_data)])


class NetvueSessionSensor(CoordinatorEntity[NetvueCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "session"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = ["supervising", "stopping", "stopped", "failed"]

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_session"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title, manufacturer="Netvue"
        )

    @property
    def available(self) -> bool:
        return (super().available and self.coordinator.data is not None
                and self.coordinator.data.session is not None)

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return data.session.state if data and data.session else None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        data = self.coordinator.data
        if data and data.session and data.session.error:
            return {"error": data.session.error}
        return {}
