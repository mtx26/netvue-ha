"""Configuration for managed camera publishers."""

import json

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import ObjectSelector

from .bridge_api import BridgeError, async_discover_cameras, async_stop_camera
from .const import (
    CONF_AI_CONFIDENCE, CONF_AI_CONFIRMATION_FRAMES, CONF_AI_ENABLED,
    CONF_AI_FPS, CONF_AI_ZONES, CONF_HOST, CONF_STREAM_NAME,
    CONF_STREAM_URL, DEFAULT_NAME, DOMAIN,
)
from .rtsp import (
    stream_name_for_host, stream_url_from_name, validate_host,
)

def _configured_hosts(hass) -> set[str]:
    return {
        entry.options.get(CONF_HOST, entry.data[CONF_HOST])
        for entry in hass.config_entries.async_entries(DOMAIN)
    }


def _duplicate(hass, host: str, url: str, exclude: str | None = None) -> bool:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == exclude:
            continue
        if (entry.options.get(CONF_HOST, entry.data[CONF_HOST]) == host
                or entry.options.get(CONF_STREAM_URL, entry.data[CONF_STREAM_URL]) == url):
            return True
    return False


class NetvueConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NetvueOptionsFlow()

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["discovery", "manual"])

    async def async_step_discovery(self, user_input=None):
        if user_input is not None and user_input.get("manual"):
            return await self.async_step_manual()
        errors = {}
        try:
            discovered = await async_discover_cameras(self.hass)
        except BridgeError:
            errors["base"] = "discovery_failed"
        else:
            configured = _configured_hosts(self.hass)
            self._discovered_hosts = [host for host in discovered if host not in configured]
            if self._discovered_hosts:
                return await self.async_step_select()
            errors["base"] = "no_new_cameras" if discovered else "no_cameras"
        return self.async_show_form(
            step_id="discovery",
            data_schema=vol.Schema({vol.Required("manual", default=False): bool}),
            errors=errors,
        )

    async def async_step_select(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input.get("manual"):
                return await self.async_step_manual()
            if user_input.get(CONF_HOST) not in self._discovered_hosts:
                errors["base"] = "invalid_input"
            else:
                result, errors = self._create_camera_entry(user_input)
                if result is not None:
                    return result
        return self.async_show_form(
            step_id="select",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Optional(CONF_HOST): vol.In(self._discovered_hosts),
                vol.Required("manual", default=False): bool,
                vol.Required(CONF_AI_ENABLED, default=True): bool,
                vol.Required(CONF_AI_CONFIDENCE, default=0.45):
                    vol.All(vol.Coerce(float), vol.Range(min=0.1, max=0.95)),
                vol.Required(CONF_AI_FPS, default=3):
                    vol.All(vol.Coerce(float), vol.Range(min=1, max=10)),
                vol.Required(CONF_AI_CONFIRMATION_FRAMES, default=3):
                    vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
                vol.Required(CONF_AI_ZONES, default={}): ObjectSelector(),
            }), errors=errors,
        )

    def _create_camera_entry(self, user_input):
        try:
            host = validate_host(user_input[CONF_HOST])
            stream_name = stream_name_for_host(host)
            url = stream_url_from_name(stream_name)
            name = user_input[CONF_NAME].strip()
        except (ValueError, KeyError, AttributeError, TypeError):
            return None, {"base": "invalid_input"}
        if not name:
            return None, {"base": "invalid_input"}
        if _duplicate(self.hass, host, url):
            return None, {"base": "already_configured"}
        ai_zones_input = user_input.get(CONF_AI_ZONES, "{}")
        try:
            ai_zones = json.loads(ai_zones_input) if isinstance(ai_zones_input, str) else ai_zones_input
        except json.JSONDecodeError:
            ai_zones = None
        if (
            not isinstance(ai_zones, dict)
            or not all(
                name in ("entrance", "doorstep", "garden", "driveway")
                and isinstance(rect, list)
                and len(rect) == 4
                and all(type(value) in (int, float) and 0 <= value <= 1 for value in rect)
                for name, rect in ai_zones.items()
            )
        ):
            return None, {"base": "invalid_ai_zones"}
        return self.async_create_entry(
            title=name,
            data={
                CONF_HOST: host,
                CONF_STREAM_NAME: stream_name,
                CONF_STREAM_URL: url,
                CONF_AI_ENABLED: user_input.get(CONF_AI_ENABLED, True),
                CONF_AI_CONFIDENCE: float(user_input.get(CONF_AI_CONFIDENCE, 0.45)),
                CONF_AI_FPS: float(user_input.get(CONF_AI_FPS, 3)),
                CONF_AI_CONFIRMATION_FRAMES: user_input.get(
                    CONF_AI_CONFIRMATION_FRAMES, 3
                ),
                CONF_AI_ZONES: ai_zones,
            },
        ), {}

    async def async_step_manual(self, user_input=None):
        errors = {}
        if user_input is not None:
            result, errors = self._create_camera_entry(user_input)
            if result is not None:
                return result
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_HOST, default=""): str,
                vol.Required(CONF_AI_ENABLED, default=True): bool,
                vol.Required(CONF_AI_CONFIDENCE, default=0.45):
                    vol.All(vol.Coerce(float), vol.Range(min=0.1, max=0.95)),
                vol.Required(CONF_AI_FPS, default=3):
                    vol.All(vol.Coerce(float), vol.Range(min=1, max=10)),
                vol.Required(CONF_AI_CONFIRMATION_FRAMES, default=3):
                    vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
                vol.Required(CONF_AI_ZONES, default={}): ObjectSelector(),
            }), errors=errors,
        )


class NetvueOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input=None):
        entry = self.config_entry
        default_host = entry.options.get(CONF_HOST, entry.data[CONF_HOST])
        default_ai_enabled = entry.options.get(
            CONF_AI_ENABLED, entry.data.get(CONF_AI_ENABLED, True)
        )
        default_ai_confidence = entry.options.get(
            CONF_AI_CONFIDENCE, entry.data.get(CONF_AI_CONFIDENCE, 0.45)
        )
        default_ai_fps = entry.options.get(
            CONF_AI_FPS, entry.data.get(CONF_AI_FPS, 3)
        )
        default_ai_confirmation = entry.options.get(
            CONF_AI_CONFIRMATION_FRAMES,
            entry.data.get(CONF_AI_CONFIRMATION_FRAMES, 3),
        )
        default_ai_zones = entry.options.get(
            CONF_AI_ZONES, entry.data.get(CONF_AI_ZONES, {})
        )
        errors = {}
        if user_input is not None:
            try:
                host = validate_host(user_input[CONF_HOST])
            except (ValueError, KeyError):
                errors["base"] = "invalid_input"
            else:
                ai_enabled = user_input.get(CONF_AI_ENABLED, default_ai_enabled)
                ai_confidence = user_input.get(CONF_AI_CONFIDENCE, default_ai_confidence)
                ai_fps = user_input.get(CONF_AI_FPS, default_ai_fps)
                ai_confirmation = user_input.get(
                    CONF_AI_CONFIRMATION_FRAMES, default_ai_confirmation
                )
                ai_zones_input = user_input.get(CONF_AI_ZONES, default_ai_zones)
                if isinstance(ai_zones_input, str):
                    try:
                        ai_zones = json.loads(ai_zones_input)
                    except json.JSONDecodeError:
                        ai_zones = None
                else:
                    ai_zones = ai_zones_input
                valid_ai_zones = (
                    isinstance(ai_zones, dict)
                    and all(
                        name in ("entrance", "doorstep", "garden", "driveway")
                        and isinstance(rect, list)
                        and len(rect) == 4
                        and all(
                            type(value) in (int, float) and 0 <= value <= 1
                            for value in rect
                        )
                        for name, rect in ai_zones.items()
                    )
                    if ai_zones is not None
                    else False
                )
                if not valid_ai_zones:
                    errors["base"] = "invalid_ai_zones"
                elif type(ai_enabled) is not bool:
                    errors["base"] = "invalid_input"
                elif type(ai_confidence) not in (int, float) or not 0.1 <= ai_confidence <= 0.95:
                    errors["base"] = "invalid_input"
                elif type(ai_fps) not in (int, float) or not 1 <= ai_fps <= 10:
                    errors["base"] = "invalid_input"
                elif type(ai_confirmation) is not int or not 1 <= ai_confirmation <= 10:
                    errors["base"] = "invalid_input"
                else:
                    stream_name = stream_name_for_host(host)
                    url = stream_url_from_name(stream_name)
                    if _duplicate(self.hass, host, url, entry.entry_id):
                        errors["base"] = "already_configured"
                    else:
                        old_host = entry.options.get(CONF_HOST, entry.data[CONF_HOST])
                        if host != old_host:
                            try:
                                await async_stop_camera(self.hass, old_host)
                            except BridgeError:
                                errors["base"] = "bridge_unavailable"
                                default_host = host
                        if not errors:
                            return self.async_create_entry(
                                title="",
                                data={
                                    **entry.options,
                                    CONF_HOST: host,
                                    CONF_STREAM_NAME: stream_name,
                                    CONF_STREAM_URL: url,
                                    CONF_AI_ENABLED: ai_enabled,
                                    CONF_AI_CONFIDENCE: float(ai_confidence),
                                    CONF_AI_FPS: float(ai_fps),
                                    CONF_AI_CONFIRMATION_FRAMES: ai_confirmation,
                                    CONF_AI_ZONES: ai_zones,
                                },
                            )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=default_host): str,
                vol.Required(CONF_AI_ENABLED, default=default_ai_enabled): bool,
                vol.Required(CONF_AI_CONFIDENCE, default=default_ai_confidence):
                    vol.All(vol.Coerce(float), vol.Range(min=0.1, max=0.95)),
                vol.Required(CONF_AI_FPS, default=default_ai_fps):
                    vol.All(vol.Coerce(float), vol.Range(min=1, max=10)),
                vol.Required(CONF_AI_CONFIRMATION_FRAMES, default=default_ai_confirmation):
                    vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
                vol.Required(CONF_AI_ZONES, default=default_ai_zones): ObjectSelector(),
            }), errors=errors,
        )
