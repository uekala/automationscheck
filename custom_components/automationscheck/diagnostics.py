"""Diagnosedaten: das letzte Prüfergebnis."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import AutomationsCheckConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AutomationsCheckConfigEntry
) -> dict[str, Any]:
    checker = entry.runtime_data
    result = checker.result
    return {
        "options": dict(entry.options),
        "result": None
        if result is None
        else {
            "checked": result.checked,
            "skipped": result.skipped,
            "finished": result.finished.isoformat(),
            "findings": result.as_dicts(show_notes=True),
        },
    }
