import json
import re
import time

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import CONF_AI_ZONES, DOMAIN

ZONE_ID_PATTERN = re.compile(r"[a-z0-9_-]{1,64}")
CATEGORIES = (
    "person", "car", "bicycle", "motorcycle", "bus", "truck", "cat", "dog", "bird",
)
ZONE_FRESHNESS_SECONDS = 6


def parse_zones(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict) or len(value) > 32:
        return None
    zones = {}
    for zone_id, zone in value.items():
        if not isinstance(zone_id, str) or not ZONE_ID_PATTERN.fullmatch(zone_id):
            return None
        if isinstance(zone, list):
            if (len(zone) != 4 or not all(_coordinate(number) for number in zone)
                    or not (zone[0] < zone[2] and zone[1] < zone[3])):
                return None
            left, top, right, bottom = zone
            zones[zone_id] = {
                "name": zone_id,
                "points": [[left, top], [right, top], [right, bottom], [left, bottom]],
            }
            continue
        if not isinstance(zone, dict) or not isinstance(name := zone.get("name"), str):
            return None
        if not name.strip() or len(name.strip()) > 80:
            return None
        points = zone.get("points")
        if (not isinstance(points, list) or not 3 <= len(points) <= 32
                or not all(isinstance(point, list) and len(point) == 2
                           and all(_coordinate(number) for number in point) for point in points)
                or len({tuple(point) for point in points}) != len(points)):
            return None
        area = sum(x * next_y - next_x * y
                   for (x, y), (next_x, next_y) in zip(points, points[1:] + points[:1]))
        if abs(area) < 1e-8:
            return None
        zones[zone_id] = zone
    return zones


def _coordinate(value):
    return type(value) in (int, float) and 0 <= value <= 1


def configured_zones(entry):
    zones = entry.options.get(CONF_AI_ZONES, entry.data.get(CONF_AI_ZONES, {}))
    if any(isinstance(zone, list) for zone in zones.values()):
        return parse_zones(zones) or {}
    return zones


def configured_zone_names(entry):
    return {
        zone_id: zone["name"]
        for zone_id, zone in configured_zones(entry).items()
    }


def zone_identity(entry_id, zone_id):
    return f"{entry_id}_zone_{zone_id}"


def zones_updated_signal(entry_id):
    return f"{DOMAIN}_zones_updated_{entry_id}"


async def async_update_detection_options(
    hass: HomeAssistant, entry: ConfigEntry, options: dict
) -> None:
    previous_zones = configured_zones(entry)
    if not hass.config_entries.async_update_entry(entry, options=options):
        return
    if entry.state is ConfigEntryState.LOADED:
        await entry.runtime_data.async_refresh()
    if previous_zones != configured_zones(entry):
        async_dispatcher_send(hass, zones_updated_signal(entry.entry_id))


def fresh_zone_frame(status):
    return (status.last_frame_at is not None
            and 0 <= time.time() - status.last_frame_at <= ZONE_FRESHNESS_SECONDS)
