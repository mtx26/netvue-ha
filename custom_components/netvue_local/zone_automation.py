from collections.abc import Mapping
from dataclasses import dataclass

import probatio

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_ENTITY_ID, CONF_OPTIONS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import DOMAIN
from .zones import CATEGORIES, configured_zones, zone_identity

CONF_CATEGORY = "category"
CONF_DURATION = "duration"
ALL_CATEGORIES = "all"

ZONE_OPTIONS = {
    probatio.Required(CONF_ENTITY_ID): cv.entity_id,
    probatio.Required(CONF_CATEGORY): probatio.All(
        probatio.EnsureList(), probatio.Length(min=1), [probatio.In((ALL_CATEGORIES, *CATEGORIES))]
    ),
}
CONDITION_SCHEMA = probatio.Schema({probatio.Required(CONF_OPTIONS): ZONE_OPTIONS})


def duration_seconds(value: object) -> int:
    if isinstance(value, str) and len(value) <= 5 and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) not in (float, int) or not 0 <= value <= 86400 or value != int(value):
        raise probatio.Invalid("Enter a whole number of seconds between 0 and 86400")
    return int(value)


TRIGGER_SCHEMA = probatio.Schema({
    probatio.Required(CONF_OPTIONS): {
        **ZONE_OPTIONS,
        probatio.Optional(CONF_DURATION, default=0): duration_seconds,
    },
})


@dataclass(frozen=True, slots=True)
class ZoneSelection:
    entity_id: str
    categories: frozenset[str]

    @classmethod
    def from_options(cls, options: Mapping) -> "ZoneSelection":
        categories = options[CONF_CATEGORY]
        return cls(
            options[CONF_ENTITY_ID],
            frozenset(CATEGORIES if ALL_CATEGORIES in categories else categories),
        )

    @classmethod
    def validate_config(cls, hass: HomeAssistant, config: dict, schema: probatio.Schema) -> dict:
        config = schema(config)
        if cls.from_options(config[CONF_OPTIONS])._resolve(hass) is None:
            raise probatio.Invalid("Select a configured Netvue detection zone")
        return config

    def _resolve(self, hass: HomeAssistant) -> tuple[ConfigEntry, str] | None:
        registered = er.async_get(hass).async_get(self.entity_id)
        if registered is None or registered.platform != DOMAIN or not registered.config_entry_id:
            return None
        entry = hass.config_entries.async_get_entry(registered.config_entry_id)
        if entry is None:
            return None
        prefix = zone_identity(entry.entry_id, "")
        zone_id = registered.unique_id.removeprefix(prefix)
        if zone_id == registered.unique_id or zone_id not in configured_zones(entry):
            return None
        return entry, zone_id

    def _resolve_loaded(self, hass: HomeAssistant) -> tuple[ConfigEntry, str] | None:
        zone = self._resolve(hass)
        if zone is not None and zone[0].state is ConfigEntryState.LOADED:
            return zone
        return None

    def is_present(self, hass: HomeAssistant) -> bool:
        zone = self._resolve_loaded(hass)
        if zone is None:
            return False
        entry, zone_id = zone
        return entry.runtime_data.live_detection.has_detection_in_zone(zone_id, self.categories)

    def match_event(self, hass: HomeAssistant, data: Mapping) -> tuple[str, str] | None:
        category = data.get("category")
        if not isinstance(category, str) or category not in self.categories:
            return None
        zone = self._resolve_loaded(hass)
        if zone is None:
            return None
        entry, zone_id = zone
        zone_ids = data.get("zone_ids")
        if (data.get("entry_id") != entry.entry_id or data.get("zone") != zone_id
                or not isinstance(zone_ids, list) or zone_id not in zone_ids
                or not entry.runtime_data.live_detection.zone_detection_available):
            return None
        return zone_id, category

    def has_presence(self, hass: HomeAssistant, zone_id: str, presence_id: str) -> bool:
        zone = self._resolve_loaded(hass)
        if zone is None or zone[1] != zone_id:
            return False
        live = zone[0].runtime_data.live_detection
        if not live.zone_detection_available:
            return False
        return any(
            isinstance(ids := detection.get("zone_presence_ids"), dict)
            and ids.get(zone_id) == presence_id
            for detection in live.data.detections
        )
