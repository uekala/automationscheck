"""Tests der Integration in einem echten Test-Home-Assistant."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.automationscheck.const import DOMAIN

MODES = ["Zuhause", "Abwesend", "Kino", "Urlaub"]

CONFLICT_A = {
    "id": "modus",
    "alias": "Modus - Aktivierung",
    "triggers": [{"trigger": "state", "entity_id": "input_select.haus_modus"}],
    "actions": [
        {
            "choose": [
                {
                    "conditions": [
                        {
                            "condition": "state",
                            "entity_id": "input_select.haus_modus",
                            "state": "Urlaub",
                        }
                    ],
                    "sequence": [
                        {"action": "cover.close_cover", "target": {"entity_id": "cover.wz"}}
                    ],
                }
            ]
        }
    ],
}
CONFLICT_B = {
    "id": "kino_aus",
    "alias": "Kino verlassen",
    "triggers": [{"trigger": "state", "entity_id": "input_select.haus_modus", "from": "Kino"}],
    "actions": [{"action": "cover.open_cover", "target": {"entity_id": "cover.wz"}}],
}
GUARDED_A = {
    "id": "alarm_folgt",
    "alias": "Alarm folgt Modus",
    "triggers": [{"trigger": "state", "entity_id": "input_select.haus_modus"}],
    "actions": [
        {
            "if": [
                {
                    "condition": "state",
                    "entity_id": "input_select.haus_modus",
                    "state": ["Abwesend", "Urlaub"],
                }
            ],
            "then": [
                {
                    "action": "alarm_control_panel.alarm_arm_away",
                    "target": {"entity_id": "alarm_control_panel.hub"},
                }
            ],
            "else": [
                {
                    "action": "alarm_control_panel.alarm_arm_home",
                    "target": {"entity_id": "alarm_control_panel.hub"},
                }
            ],
        }
    ],
}
GUARDED_B = {
    "id": "modus_folgt",
    "alias": "Modus folgt Alarm",
    "triggers": [{"trigger": "state", "entity_id": "alarm_control_panel.hub"}],
    "actions": [
        {
            "choose": [
                {
                    "conditions": [
                        {
                            "condition": "state",
                            "entity_id": "alarm_control_panel.hub",
                            "state": "armed_home",
                        },
                        {
                            "condition": "not",
                            "conditions": [
                                {
                                    "condition": "state",
                                    "entity_id": "input_select.haus_modus",
                                    "state": ["Zuhause", "Kino"],
                                }
                            ],
                        },
                    ],
                    "sequence": [
                        {
                            "action": "input_select.select_option",
                            "data": {"option": "Zuhause"},
                            "target": {"entity_id": "input_select.haus_modus"},
                        }
                    ],
                }
            ]
        }
    ],
}
BROKEN_ID = {
    "id": "kaputt",
    "alias": "Licht Bad",
    "triggers": [{"trigger": "state", "entity_id": "binary_sensor.bad", "to": "on", "id": "an"}],
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
SELF_TRIGGER = {
    "id": "toggle",
    "alias": "Umschalter",
    "triggers": [{"trigger": "state", "entity_id": "light.flur"}],
    "actions": [{"action": "light.toggle", "target": {"entity_id": "{{ trigger.entity_id }}"}}],
}


async def _setup_home(hass: HomeAssistant, automations: list[dict]) -> None:
    assert await async_setup_component(
        hass, "input_select", {"input_select": {"haus_modus": {"options": MODES}}}
    )
    assert await async_setup_component(hass, "automation", {"automation": automations})
    await hass.async_block_till_done()


async def _setup_entry(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="Automationscheck", data={}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _issues(hass: HomeAssistant) -> dict[str, ir.IssueEntry]:
    return {
        issue_id: issue
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN
    }


async def test_config_flow_single_instance(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    again = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert again["type"] is FlowResultType.ABORT


async def test_findings_become_repairs(hass: HomeAssistant) -> None:
    await _setup_home(hass, [CONFLICT_A, CONFLICT_B, GUARDED_A, GUARDED_B, BROKEN_ID])
    entry = await _setup_entry(hass)
    assert entry.state is ConfigEntryState.LOADED

    issues = _issues(hass)
    kinds = sorted(issue.translation_key for issue in issues.values())
    assert kinds == ["conflict", "unknown_trigger_id"]  # abgesicherte Sync-Schleife nicht

    conflict = next(i for i in issues.values() if i.translation_key == "conflict")
    assert conflict.severity is ir.IssueSeverity.WARNING
    placeholders = conflict.translation_placeholders
    assert {placeholders["automation_a"], placeholders["automation_b"]} == {
        "Modus - Aktivierung",
        "Kino verlassen",
    }
    assert placeholders["situation"] == "`input_select.haus_modus` = Urlaub"
    broken = next(i for i in issues.values() if i.translation_key == "unknown_trigger_id")
    assert broken.severity is ir.IssueSeverity.ERROR
    assert broken.translation_placeholders["ids"] == "weg"

    state = hass.states.get("sensor.automationscheck_findings")
    assert state.state == "2"
    assert state.attributes["checked"] == 5
    assert state.attributes["errors"] == 1
    assert {f["type"] for f in state.attributes["findings"]} == {"conflict", "unknown_trigger_id"}


async def test_notes_only_with_option(hass: HomeAssistant) -> None:
    await _setup_home(hass, [SELF_TRIGGER])
    await _setup_entry(hass)
    assert _issues(hass) == {}
    assert hass.states.get("sensor.automationscheck_findings").attributes["notes"] == 1


async def test_notes_shown_when_enabled(hass: HomeAssistant) -> None:
    await _setup_home(hass, [SELF_TRIGGER])
    await _setup_entry(hass, show_notes=True)
    assert [i.translation_key for i in _issues(hass).values()] == ["self_trigger"]


async def test_disabled_automations_are_skipped(hass: HomeAssistant) -> None:
    await _setup_home(hass, [CONFLICT_A, CONFLICT_B])
    await hass.services.async_call(
        "automation", "turn_off", {"entity_id": "automation.kino_verlassen"}, blocking=True
    )
    entry = await _setup_entry(hass)
    assert _issues(hass) == {}
    assert entry.runtime_data.result.checked == 1


async def test_fixed_automation_clears_repair_after_reload(hass: HomeAssistant, freezer) -> None:
    await _setup_home(hass, [CONFLICT_A, CONFLICT_B])
    await _setup_entry(hass)
    assert len(_issues(hass)) == 1

    fixed = {**CONFLICT_B, "triggers": [{**CONFLICT_B["triggers"][0], "to": "Zuhause"}]}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "homeassistant.config.load_yaml_config_file",
            lambda *_: {"automation": [CONFLICT_A, fixed]},
        )
        await hass.services.async_call("automation", "reload", blocking=True)
        await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=15))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get("automation.kino_verlassen") is not None
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.runtime_data.result.checked == 2  # neu geprüft, beide noch da
    assert _issues(hass) == {}


async def test_run_action_returns_findings(hass: HomeAssistant) -> None:
    await _setup_home(hass, [BROKEN_ID])
    await _setup_entry(hass)
    response = await hass.services.async_call(
        DOMAIN, "run", {}, blocking=True, return_response=True
    )
    assert response["checked"] == 1
    assert response["findings"][0]["type"] == "unknown_trigger_id"
    assert response["findings"][0]["automations"] == ["Licht Bad"]


async def test_remove_entry_deletes_repairs(hass: HomeAssistant) -> None:
    await _setup_home(hass, [BROKEN_ID])
    entry = await _setup_entry(hass)
    assert _issues(hass)
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _issues(hass) == {}
