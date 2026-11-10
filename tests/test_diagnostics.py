"""Diagnosedaten und Verhalten bei nicht lesbaren Automationen."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.automationscheck.const import DOMAIN

BROKEN_ID = {
    "id": "licht_bad",
    "alias": "Licht Bad",
    "triggers": [
        {"trigger": "state", "entity_id": "binary_sensor.bad", "to": "on", "id": "da"},
    ],
    "actions": [
        {
            "choose": [
                {
                    "conditions": [{"condition": "trigger", "id": "weg"}],
                    "sequence": [
                        {"action": "light.turn_off", "target": {"entity_id": "light.bad"}}
                    ],
                }
            ]
        }
    ],
}
TOGGLE = {
    "id": "toggle",
    "alias": "Umschalter",
    "triggers": [{"trigger": "state", "entity_id": "light.flur"}],
    "actions": [{"action": "light.toggle", "target": {"entity_id": "{{ trigger.entity_id }}"}}],
}


async def _setup(hass: HomeAssistant, automations: list[dict], **options) -> MockConfigEntry:
    assert await async_setup_component(hass, "automation", {"automation": automations})
    await hass.async_block_till_done()
    entry = MockConfigEntry(domain=DOMAIN, title="Automationscheck", data={}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_diagnostics_contain_last_result_with_notes(hass: HomeAssistant, hass_client) -> None:
    entry = await _setup(hass, [BROKEN_ID, TOGGLE], include_disabled=True)

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics["options"] == {"include_disabled": True}
    result = diagnostics["result"]
    assert result["checked"] == 2
    assert result["skipped"] == []
    assert result["finished"].endswith("+00:00")
    # Hinweise stehen in den Diagnosedaten, auch wenn sie keine Reparatur erzeugen.
    assert {finding["type"] for finding in result["findings"]} == {
        "unknown_trigger_id",
        "idle_trigger",
        "self_trigger",
    }


async def test_diagnostics_before_first_check(hass: HomeAssistant, hass_client) -> None:
    entry = await _setup(hass, [BROKEN_ID])
    entry.runtime_data.result = None

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diagnostics == {"options": {}, "result": None}


async def test_home_assistant_still_provides_raw_config(hass: HomeAssistant) -> None:
    """Frühwarnung: Die Prüfung liest die Konfiguration aus ``raw_config``.

    Das ist keine offizielle Schnittstelle von Home Assistant. Fällt dieser
    Test nach einem Update der Testumgebung um, muss die Datenquelle angepasst
    werden, bevor ein Release für die neue HA-Version erscheint.
    """
    assert await async_setup_component(hass, "automation", {"automation": [BROKEN_ID]})
    await hass.async_block_till_done()

    entity = next(iter(hass.data["automation"].entities))

    assert entity.raw_config["id"] == "licht_bad"


async def test_unreadable_automations_are_listed_as_skipped(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "automation", {"automation": [BROKEN_ID, TOGGLE]})
    await hass.async_block_till_done()
    for entity in hass.data["automation"].entities:
        if entity.entity_id == "automation.licht_bad":
            entity.raw_config = None

    entry = MockConfigEntry(domain=DOMAIN, title="Automationscheck", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = entry.runtime_data.result
    assert result.checked == 1
    assert result.skipped == ["Licht Bad"]
    state = hass.states.get("sensor.automationscheck_findings")
    assert state.attributes["skipped"] == ["Licht Bad"]
