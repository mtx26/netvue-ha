"""Session status reported by the local Go bridge."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CameraData, NetvueCoordinator, NetvueEntity


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
        NetvueRecordingSensor(entry, entry.runtime_data),
    ])


class NetvueSessionSensor(NetvueEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "session"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = ["supervising", "stopping", "stopped", "failed"]

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_session"

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


class NetvueDiagnosticSensor(NetvueEntity, SensorEntity):
    """Translate bridge/session status into a concise, actionable diagnosis."""

    _attr_has_entity_name = True
    _attr_translation_key = "diagnostic"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = _DIAGNOSTIC_OPTIONS

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_diagnostic"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return _diagnostic_state(data) if data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data
        if data is None:
            return {}
        attributes: dict[str, object] = {}
        if data.session:
            if data.session.connection_port is not None:
                attributes["connection_port"] = data.session.connection_port
            if (metadata := data.session.metadata) is not None:
                if metadata.wifi_ssid is not None:
                    attributes["wifi_ssid"] = metadata.wifi_ssid
                if metadata.wifi_signal_level is not None:
                    attributes["wifi_signal_level"] = metadata.wifi_signal_level
                if metadata.capabilities:
                    attributes["capabilities"] = metadata.capabilities
        if data.manager_error:
            attributes["details"] = data.manager_error
        elif data.session and data.session.error:
            attributes["details"] = data.session.error
        return attributes


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


class NetvueRecordingSensor(NetvueEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "recording"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = ["inactive", "manual", "continuous"]

    def __init__(self, entry: ConfigEntry, coordinator: NetvueCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_recording"

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data is not None and data.recording is not None

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        if not data or not data.recording:
            return "inactive"
        if data.recording.manual:
            return "manual"
        if data.recording.continuous:
            return "continuous"
        return "inactive"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data
        if not data or not data.recording:
            return {}
        return {
            "netvue_entry_id": self.coordinator._entry.entry_id,
            "netvue_host": self.coordinator.camera.host,
            "active": data.recording.active,
            "manual": data.recording.manual,
            "continuous": data.recording.continuous,
            "error": data.recording.error,
        }
