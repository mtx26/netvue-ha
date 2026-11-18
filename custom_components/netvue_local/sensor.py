"""Session status reported by the local Go bridge."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CameraData, NetvueCoordinator


_DIAGNOSTIC_OPTIONS = [
    "healthy",
    "stream_unavailable",
    "bridge_unavailable",
    "authentication_failed",
    "connection_failed",
    "publication_failed",
    "protocol_error",
    "video_interrupted",
    "session_stopping",
    "session_stopped",
    "session_failed",
    "session_unknown",
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([
        NetvueSessionSensor(entry, entry.runtime_data),
        NetvueDiagnosticSensor(entry, entry.runtime_data),
    ])


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


class NetvueDiagnosticSensor(CoordinatorEntity[NetvueCoordinator], SensorEntity):
    """Translate bridge/session status into a concise, actionable diagnosis."""

    _attr_has_entity_name = True
    _attr_translation_key = "diagnostic"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = _DIAGNOSTIC_OPTIONS

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_diagnostic"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title, manufacturer="Netvue"
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return _diagnostic_state(data) if data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        data = self.coordinator.data
        if data is None:
            return {}
        if data.manager_error:
            return {"details": data.manager_error}
        if data.session and data.session.error:
            return {"details": data.session.error}
        return {}


def _diagnostic_state(data: CameraData) -> str:
    if data.manager_error:
        return "bridge_unavailable"
    session = data.session
    if session is None:
        return "session_unknown"
    if session.state == "stopping":
        return "session_stopping"
    if session.state == "stopped":
        return "session_stopped"
    if session.state == "failed":
        error = (session.error or "").casefold()
        if "authentication rejected" in error:
            return "authentication_failed"
        if "connection error" in error:
            return "connection_failed"
        if "publication failed" in error or "video publication" in error:
            return "publication_failed"
        if ("invalid" in error and ("protocol" in error or "h.264" in error
                                   or "netvue" in error)
                or "unsupported netvue" in error):
            return "protocol_error"
        if "not ready" in error or "interrupted" in error:
            return "video_interrupted"
        return "session_failed"
    if not data.rtsp_available:
        return "stream_unavailable"
    return "healthy"
