"""Configuration for managed camera publishers."""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .bridge_api import BridgeError, async_discover_cameras, async_stop_camera
from .const import (
    CONF_HOST, CONF_STREAM_NAME, CONF_STREAM_URL, DEFAULT_NAME, DOMAIN,
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
        return self.async_create_entry(
            title=name,
            data={CONF_HOST: host, CONF_STREAM_NAME: stream_name, CONF_STREAM_URL: url},
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
            }), errors=errors,
        )


class NetvueOptionsFlow(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input=None):
        entry = self.config_entry
        default_host = entry.options.get(CONF_HOST, entry.data[CONF_HOST])
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
                else:
                    old_host = entry.options.get(CONF_HOST, entry.data[CONF_HOST])
                    try:
                        await async_stop_camera(self.hass, old_host)
                    except BridgeError:
                        errors["base"] = "bridge_unavailable"
                        default_host = host
                    else:
                        return self.async_create_entry(
                            title="",
                            data={
                                CONF_HOST: host,
                                CONF_STREAM_NAME: stream_name,
                                CONF_STREAM_URL: url,
                            },
                        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=default_host): str,
            }), errors=errors,
        )
