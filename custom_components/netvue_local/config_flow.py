"""Manual configuration for managed camera publishers."""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .bridge_api import BridgeError, async_stop_camera
from .const import (
    CONF_HOST, CONF_STREAM_NAME, CONF_STREAM_URL,
    DEFAULT_NAME, DOMAIN,
)
from .rtsp import (
    stream_name_for_host, stream_url_from_name, validate_host,
)

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
        errors = {}
        if user_input is not None:
            try:
                host = validate_host(user_input[CONF_HOST])
                stream_name = stream_name_for_host(host)
                url = stream_url_from_name(stream_name)
            except (ValueError, KeyError):
                errors["base"] = "invalid_input"
            else:
                if _duplicate(self.hass, host, url):
                    errors["base"] = "already_configured"
                elif not user_input[CONF_NAME].strip():
                    errors["base"] = "invalid_input"
                else:
                    return self.async_create_entry(
                        title=user_input[CONF_NAME].strip(),
                        data={CONF_HOST: host, CONF_STREAM_NAME: stream_name,
                              CONF_STREAM_URL: url},
                    )
        return self.async_show_form(
            step_id="user",
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
                            data={CONF_HOST: host, CONF_STREAM_NAME: stream_name,
                                  CONF_STREAM_URL: url},
                        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=default_host): str,
            }), errors=errors,
        )
