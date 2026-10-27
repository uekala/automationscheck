"""Einrichtung und Optionen für Automationscheck."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import CONF_INCLUDE_DISABLED, CONF_SHOW_NOTES, DOMAIN


class AutomationsCheckConfigFlow(ConfigFlow, domain=DOMAIN):
    """Eine Instanz pro Home Assistant."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Automationscheck", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AutomationsCheckOptionsFlow()


class AutomationsCheckOptionsFlow(OptionsFlow):
    """Welche Befunde als Reparatur erscheinen."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(CONF_SHOW_NOTES, default=options.get(CONF_SHOW_NOTES, False)): bool,
                vol.Required(
                    CONF_INCLUDE_DISABLED, default=options.get(CONF_INCLUDE_DISABLED, False)
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
