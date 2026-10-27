"""Automationscheck – findet Schleifen und Gegenspieler in Automationen."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .checker import AutomationsCheck, async_remove_issues
from .const import DOMAIN, SERVICE_RUN

PLATFORMS: list[Platform] = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type AutomationsCheckConfigEntry = ConfigEntry[AutomationsCheck]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def _async_run(call: ServiceCall) -> ServiceResponse:
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]
        if not entries:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
        checker: AutomationsCheck = entries[0].runtime_data
        result = await checker.async_run()
        return {
            "checked": result.checked,
            "skipped": result.skipped,
            "findings": result.as_dicts(checker.show_notes),
        }

    hass.services.async_register(
        DOMAIN, SERVICE_RUN, _async_run, supports_response=SupportsResponse.OPTIONAL
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AutomationsCheckConfigEntry) -> bool:
    checker = AutomationsCheck(hass, entry)
    entry.runtime_data = checker
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    checker.async_start()
    entry.async_on_unload(checker.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AutomationsCheckConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    async_remove_issues(hass)


async def _async_update_listener(hass: HomeAssistant, entry: AutomationsCheckConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
