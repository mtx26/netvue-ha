"""User-initiated discovery and configuration for existing publishers."""

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.network import async_get_adapters
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .const import (
    CONF_CONFIRM, CONF_HOST, CONF_SOURCE, CONF_STREAM_NAME, CONF_STREAM_URL,
    DEFAULT_NAME, DEFAULT_STREAM_NAME, DOMAIN,
)
from .discovery import async_discover, local_network
from .rtsp import async_probe_stream, stream_name_from_url, stream_url_from_name, validate_host

_LOGGER = logging.getLogger(__name__)


def _duplicate(hass, host: str, url: str, exclude: str | None = None) -> bool:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == exclude:
            continue
        if (entry.options.get(CONF_HOST, entry.data[CONF_HOST]) == host
                or entry.options.get(CONF_STREAM_URL, entry.data[CONF_STREAM_URL]) == url):
            return True
    return False


async def _sources(hass) -> dict[str, tuple[str, int]]:
    sources = {}
    for adapter in await async_get_adapters(hass):
        if not adapter["enabled"]:
            continue
        for item in adapter["ipv4"]:
            address, prefix = item["address"], item["network_prefix"]
            try:
                network = local_network(address, prefix)
            except ValueError:
                continue
            sources[f'{adapter["name"]}: {address} ({network})'] = (address, prefix)
    return sources


class NetvueConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._sources = {}
        self._candidates = []
        self._host = ""
        self._data = {}
        self._status = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return NetvueOptionsFlow()

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["discover", "manual"])

    async def async_step_discover(self, user_input=None):
        self._sources = await _sources(self.hass)
        errors = {}
        if user_input is not None:
            source = self._sources.get(user_input[CONF_SOURCE])
            if source is None:
                errors["base"] = "invalid_interface"
            else:
                try:
                    self._candidates = await async_discover(*source)
                except OSError:
                    _LOGGER.warning("Local discovery failed")
                    errors["base"] = "discovery_failed"
                else:
                    if self._candidates:
                        return await self.async_step_select()
                    errors["base"] = "no_devices"
        return self.async_show_form(
            step_id="discover",
            data_schema=vol.Schema({vol.Required(CONF_SOURCE): vol.In(self._sources)}),
            errors=errors,
        )

    async def async_step_select(self, user_input=None):
        choices = {candidate.host: candidate.host for candidate in self._candidates}
        if user_input is not None and user_input.get(CONF_HOST) in choices:
            self._host = user_input[CONF_HOST]
            return await self.async_step_manual()
        return self.async_show_form(
            step_id="select", data_schema=vol.Schema({vol.Required(CONF_HOST): vol.In(choices)})
        )

    async def async_step_manual(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                host = validate_host(user_input[CONF_HOST])
                url = stream_url_from_name(user_input[CONF_STREAM_NAME])
            except (ValueError, KeyError):
                errors["base"] = "invalid_input"
            else:
                if _duplicate(self.hass, host, url):
                    errors["base"] = "already_configured"
                elif not user_input[CONF_NAME].strip():
                    errors["base"] = "invalid_input"
                else:
                    self._data = {CONF_HOST: host, CONF_STREAM_URL: url,
                                  CONF_NAME: user_input[CONF_NAME].strip()}
                    return await self.async_step_confirm()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_HOST, default=self._host): str,
                vol.Required(CONF_STREAM_NAME, default=DEFAULT_STREAM_NAME): str,
            }), errors=errors,
        )

    async def async_step_confirm(self, user_input=None):
        if not self._data:
            return self.async_abort(reason="invalid_input")
        errors = {}
        if user_input is not None:
            if not user_input.get(CONF_CONFIRM):
                errors["base"] = "confirmation_required"
            elif _duplicate(self.hass, self._data[CONF_HOST], self._data[CONF_STREAM_URL]):
                errors["base"] = "already_configured"
            else:
                available = await async_probe_stream(self._data[CONF_STREAM_URL])
                self._status = "H.264 SDP" if available else "indisponible / unavailable"
                return await self.async_step_status()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "host": self._data[CONF_HOST],
                "url": self._data[CONF_STREAM_URL],
            },
            data_schema=vol.Schema({vol.Required(CONF_CONFIRM, default=False): bool}),
            errors=errors,
        )

    async def async_step_status(self, user_input=None):
        if not self._data:
            return self.async_abort(reason="invalid_input")
        if user_input is not None:
            if _duplicate(self.hass, self._data[CONF_HOST], self._data[CONF_STREAM_URL]):
                return self.async_abort(reason="already_configured")
            return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)
        return self.async_show_form(step_id="status", data_schema=vol.Schema({}),
                                    description_placeholders={"status": self._status})


class NetvueOptionsFlow(config_entries.OptionsFlowWithReload):
    def __init__(self) -> None:
        self._data = {}
        self._status = ""

    async def async_step_init(self, user_input=None):
        entry = self.config_entry
        old_url = entry.options.get(CONF_STREAM_URL, entry.data[CONF_STREAM_URL])
        old_name = stream_name_from_url(old_url)
        errors = {}
        if user_input is not None:
            try:
                host = validate_host(user_input[CONF_HOST])
                name = user_input.get(CONF_STREAM_NAME, "")
                url = old_url if old_name is None and not name else stream_url_from_name(name)
            except (ValueError, KeyError):
                errors["base"] = "invalid_input"
            else:
                if _duplicate(self.hass, host, url, entry.entry_id):
                    errors["base"] = "already_configured"
                else:
                    self._data = {CONF_HOST: host, CONF_STREAM_URL: url}
                    return await self.async_step_confirm()
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST, default=entry.options.get(
                    CONF_HOST, entry.data[CONF_HOST])): str,
                (vol.Required(CONF_STREAM_NAME, default=old_name) if old_name is not None
                 else vol.Optional(CONF_STREAM_NAME)): str,
            }), errors=errors, description_placeholders={"url": old_url},
        )

    async def async_step_confirm(self, user_input=None):
        if not self._data:
            return self.async_abort(reason="invalid_input")
        errors = {}
        if user_input is not None:
            if not user_input.get(CONF_CONFIRM):
                errors["base"] = "confirmation_required"
            elif _duplicate(self.hass, self._data[CONF_HOST], self._data[CONF_STREAM_URL],
                            self.config_entry.entry_id):
                errors["base"] = "already_configured"
            else:
                available = await async_probe_stream(self._data[CONF_STREAM_URL])
                self._status = "H.264 SDP" if available else "indisponible / unavailable"
                return await self.async_step_status()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"host": self._data[CONF_HOST],
                                      "url": self._data[CONF_STREAM_URL]},
            data_schema=vol.Schema({vol.Required(CONF_CONFIRM, default=False): bool}),
            errors=errors,
        )

    async def async_step_status(self, user_input=None):
        if not self._data:
            return self.async_abort(reason="invalid_input")
        if user_input is not None:
            if _duplicate(self.hass, self._data[CONF_HOST], self._data[CONF_STREAM_URL],
                          self.config_entry.entry_id):
                return self.async_abort(reason="already_configured")
            return self.async_create_entry(title="", data=self._data)
        return self.async_show_form(step_id="status", data_schema=vol.Schema({}),
                                    description_placeholders={"status": self._status,
                                                              "url": self._data[CONF_STREAM_URL]})
