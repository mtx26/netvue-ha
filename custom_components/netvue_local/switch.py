"""Recording switches for the Netvue Local integration."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .bridge_api import BridgeError
from .coordinator import NetvueCoordinator, NetvueEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([NetvueContinuousRecordingSwitch(entry, entry.runtime_data)])


class NetvueContinuousRecordingSwitch(NetvueEntity, SwitchEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "continuous_recording"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_continuous_recording"

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and data.recording is not None

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        return bool(data and data.recording and data.recording.continuous)

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_continuous(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_continuous(False)

    async def _set_continuous(self, enabled: bool) -> None:
        try:
            await self.coordinator.camera.async_set_continuous_recording(enabled)
        except BridgeError as exc:
            raise HomeAssistantError(
                f"Unable to change continuous recording: {exc}"
            ) from exc
        await self.coordinator.async_request_refresh()
