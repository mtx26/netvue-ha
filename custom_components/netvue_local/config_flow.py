"""Configuration for managed camera publishers."""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import ObjectSelector, TextSelector, TextSelectorConfig, TextSelectorType

from .bridge_api import BridgeError, async_discover_cameras, async_stop_camera
from .const import (
    CONF_AI_CONFIDENCE, CONF_AI_CONFIRMATION_FRAMES, CONF_AI_ENABLED,
    CONF_AI_FPS, CONF_AI_ZONES, CONF_HOST, CONF_STREAM_NAME,
    CONF_STREAM_URL, DEFAULT_AI_CONFIDENCE, DEFAULT_NAME, DOMAIN,
    CONF_RETENTION_DAYS, CONF_STORAGE_LIMIT_GB, CONF_TURN_ENABLED,
    CONF_TURN_KEY_ID, CONF_TURN_API_TOKEN, CONF_TURN_CLEAR_TOKEN,
    GLOBAL_SETTINGS_DEFAULTS, OVERLAY_DEFAULTS, OVERLAY_OPTIONS, SETTINGS_PANEL_PATH,
)
from .zones import async_update_detection_options, configured_zones, parse_zones
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


def _detection_fields(defaults: dict | None = None) -> dict:
    if defaults is None:
        defaults = {
            CONF_AI_ENABLED: True,
            CONF_AI_CONFIDENCE: DEFAULT_AI_CONFIDENCE,
            CONF_AI_FPS: 3,
            CONF_AI_CONFIRMATION_FRAMES: 3,
        }
    return {
        vol.Required(CONF_AI_ENABLED, default=defaults[CONF_AI_ENABLED]): bool,
        vol.Required(CONF_AI_CONFIDENCE, default=defaults[CONF_AI_CONFIDENCE]):
            vol.All(vol.Coerce(float), vol.Range(min=0.1, max=0.95)),
        vol.Required(CONF_AI_FPS, default=defaults[CONF_AI_FPS]):
            vol.All(vol.Coerce(float), vol.Range(min=1, max=10)),
        vol.Required(CONF_AI_CONFIRMATION_FRAMES, default=defaults[CONF_AI_CONFIRMATION_FRAMES]):
            vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
    }


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
                **_detection_fields(),
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
        ai_zones = parse_zones(user_input.get(CONF_AI_ZONES, "{}"))
        if ai_zones is None:
            return None, {"base": "invalid_ai_zones"}
        return self.async_create_entry(
            title=name,
            data={
                CONF_HOST: host,
                CONF_STREAM_NAME: stream_name,
                CONF_STREAM_URL: url,
                CONF_AI_ENABLED: user_input.get(CONF_AI_ENABLED, True),
                CONF_AI_CONFIDENCE: float(user_input.get(CONF_AI_CONFIDENCE, DEFAULT_AI_CONFIDENCE)),
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
                **_detection_fields(),
                vol.Required(CONF_AI_ZONES, default={}): ObjectSelector(),
            }), errors=errors,
        )


class NetvueOptionsFlow(config_entries.OptionsFlowWithReload):
    """Camera options split into focused pages reachable from a menu."""

    def _option(self, key, default):
        entry = self.config_entry
        return entry.options.get(key, entry.data.get(key, default))

    def _save(self, values):
        return self.async_create_entry(title="", data={**self.config_entry.options, **values})

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["camera", "detection", "zones", "player", "global_settings"],
        )

    async def async_step_camera(self, user_input=None):
        entry = self.config_entry
        old_host = entry.options.get(CONF_HOST, entry.data[CONF_HOST])
        default_host = old_host
        errors = {}
        if user_input is not None:
            try:
                host = validate_host(user_input[CONF_HOST])
            except (ValueError, KeyError):
                errors["base"] = "invalid_input"
            else:
                stream_name = stream_name_for_host(host)
                url = stream_url_from_name(stream_name)
                if _duplicate(self.hass, host, url, entry.entry_id):
                    errors["base"] = "already_configured"
                elif host != old_host:
                    try:
                        await async_stop_camera(self.hass, old_host)
                    except BridgeError:
                        errors["base"] = "bridge_unavailable"
                        default_host = host
                if not errors:
                    return self._save({
                        CONF_HOST: host, CONF_STREAM_NAME: stream_name, CONF_STREAM_URL: url,
                    })
        return self.async_show_form(
            step_id="camera",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=default_host): str}),
            errors=errors,
        )

    async def async_step_detection(self, user_input=None):
        defaults = {
            CONF_AI_ENABLED: self._option(CONF_AI_ENABLED, True),
            CONF_AI_CONFIDENCE: self._option(CONF_AI_CONFIDENCE, DEFAULT_AI_CONFIDENCE),
            CONF_AI_FPS: self._option(CONF_AI_FPS, 3),
            CONF_AI_CONFIRMATION_FRAMES: self._option(CONF_AI_CONFIRMATION_FRAMES, 3),
        }
        errors = {}
        if user_input is not None:
            values = {key: user_input.get(key, value) for key, value in defaults.items()}
            confidence = values[CONF_AI_CONFIDENCE]
            fps = values[CONF_AI_FPS]
            confirmation = values[CONF_AI_CONFIRMATION_FRAMES]
            if (type(values[CONF_AI_ENABLED]) is not bool
                    or type(confidence) not in (int, float) or not 0.1 <= confidence <= 0.95
                    or type(fps) not in (int, float) or not 1 <= fps <= 10
                    or type(confirmation) is not int or not 1 <= confirmation <= 10):
                errors["base"] = "invalid_input"
            else:
                values[CONF_AI_CONFIDENCE] = float(confidence)
                values[CONF_AI_FPS] = float(fps)
                return self._save(values)
        return self.async_show_form(
            step_id="detection",
            data_schema=vol.Schema(_detection_fields(defaults)),
            errors=errors,
        )

    async def async_step_zones(self, user_input=None):
        errors = {}
        if user_input is not None:
            zones = parse_zones(user_input.get(CONF_AI_ZONES, {}))
            if zones is None:
                errors["base"] = "invalid_ai_zones"
            else:
                entry = self.config_entry
                options = {**entry.options, CONF_AI_ZONES: zones}
                self.automatic_reload = False
                await async_update_detection_options(self.hass, entry, options)
                return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="zones",
            data_schema=vol.Schema({
                vol.Required(CONF_AI_ZONES, default=configured_zones(self.config_entry)):
                    ObjectSelector(),
            }),
            errors=errors,
            description_placeholders={
                "zones_url": f"/{SETTINGS_PANEL_PATH}?config_entry={self.config_entry.entry_id}&page=zones",
            },
        )

    async def async_step_player(self, user_input=None):
        defaults = {key: self._option(key, OVERLAY_DEFAULTS[key]) for key in OVERLAY_OPTIONS}
        errors = {}
        if user_input is not None:
            values = {key: user_input.get(key, value) for key, value in defaults.items()}
            if any(type(value) is not bool for value in values.values()):
                errors["base"] = "invalid_input"
            else:
                return self._save(values)
        return self.async_show_form(
            step_id="player",
            data_schema=vol.Schema({
                vol.Required(key, default=value): bool for key, value in defaults.items()
            }),
            errors=errors,
        )

    async def async_step_global_settings(self, user_input=None):
        from . import async_get_settings

        settings = async_get_settings(self.hass)
        errors = {}
        if user_input is not None:
            try:
                await settings.async_update({
                    key: value for key, value in user_input.items()
                    if key in GLOBAL_SETTINGS_DEFAULTS or key == CONF_TURN_CLEAR_TOKEN
                })
            except ValueError:
                errors["base"] = "invalid_global_settings"
            except BridgeError:
                errors["base"] = "bridge_unavailable"
            else:
                return self._save({})
        current = await settings.async_public()
        return self.async_show_form(
            step_id="global_settings",
            data_schema=vol.Schema({
                vol.Required(CONF_RETENTION_DAYS, default=current[CONF_RETENTION_DAYS]):
                    vol.All(vol.Coerce(float), vol.Range(min=1e-9 / 86400, max=106751)),
                vol.Required(CONF_STORAGE_LIMIT_GB, default=current[CONF_STORAGE_LIMIT_GB]):
                    vol.All(vol.Coerce(float), vol.Range(min=0, max=1048576)),
                vol.Required(CONF_TURN_ENABLED, default=current[CONF_TURN_ENABLED]): bool,
                vol.Required(CONF_TURN_KEY_ID, default=current[CONF_TURN_KEY_ID]): str,
                vol.Optional(CONF_TURN_API_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_TURN_CLEAR_TOKEN, default=False): bool,
            }),
            errors=errors,
            description_placeholders={
                "turn_token_status": "✓" if current["turn_token_configured"] else "—",
            },
        )
