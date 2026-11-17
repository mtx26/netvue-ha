"""Explicit restart of a stopped or failed camera session."""

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .bridge_api import BridgeError
from .const import DOMAIN
from .coordinator import NetvueCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([NetvueRestartButton(entry, entry.runtime_data)])


class NetvueRestartButton(CoordinatorEntity[NetvueCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "restart_session"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_restart"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title, manufacturer="Netvue"
        )

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return (super().available and data is not None and data.session is not None
                and data.session.state in ("failed", "stopped"))

    async def async_press(self) -> None:
        try:
            await self.coordinator.camera.async_start()
        except BridgeError as exc:
            raise HomeAssistantError(f"Unable to restart the bridge session: {exc}") from exc
        await self.coordinator.async_request_refresh()
