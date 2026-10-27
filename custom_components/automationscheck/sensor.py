"""Sensor mit der Zahl der Befunde."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AutomationsCheckConfigEntry
from .checker import AutomationsCheck
from .const import DOMAIN, SIGNAL_UPDATED
from .engine import ERROR, NOTE, WARNING


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AutomationsCheckConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([FindingsSensor(entry.runtime_data)])


class FindingsSensor(SensorEntity):
    """Anzahl der Befunde, Details in den Attributen."""

    _attr_has_entity_name = True
    _attr_translation_key = "findings"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False

    def __init__(self, checker: AutomationsCheck) -> None:
        self._checker = checker
        entry = checker.entry
        self._attr_unique_id = f"{entry.entry_id}_findings"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Automationscheck",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_UPDATED}_{self._checker.entry.entry_id}",
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._checker.result is not None

    @property
    def native_value(self) -> int | None:
        result = self._checker.result
        if result is None:
            return None
        return len(result.shown(self._checker.show_notes))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        result = self._checker.result
        if result is None:
            return None
        counts = {ERROR: 0, WARNING: 0, NOTE: 0}
        for finding in result.findings:
            counts[finding.severity] += 1
        return {
            "errors": counts[ERROR],
            "warnings": counts[WARNING],
            "notes": counts[NOTE],
            "checked": result.checked,
            "skipped": result.skipped,
            "last_check": result.finished.isoformat(),
            "findings": result.as_dicts(self._checker.show_notes),
        }
