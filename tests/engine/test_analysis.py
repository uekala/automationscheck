"""Tests für die Automations-Analyse mit kleinen, künstlichen Automationen."""

from __future__ import annotations

from custom_components.automationscheck.engine import (
    CONFLICT,
    ERROR,
    IDLE_TRIGGER,
    LOOP,
    NOTE,
    SELF_TRIGGER,
    UNKNOWN_TRIGGER_ID,
    WARNING,
    analyse,
    build_unit,
    inline_scripts,
)

MODES = {"input_select.haus_modus": frozenset({"Zuhause", "Abwesend", "Kino", "Urlaub"})}


def units(*configs, scripts=()):
    result = {}
    for index, config in enumerate(configs):
        entity_id = f"automation.a{index}"
        result[entity_id] = build_unit(
            entity_id, config.get("alias", entity_id), "automation", config
        )
    for name, config in scripts:
        result[f"script.{name}"] = build_unit(f"script.{name}", name, "script", config)
    inline_scripts(result)
    return list(result.values())


def kinds(findings):
    return [(f.severity, f.kind) for f in findings]


GUARDED_A = {
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


def test_guarded_sync_is_not_a_loop_when_options_are_known():
    assert kinds(analyse(units(GUARDED_A, GUARDED_B), MODES)) == []


def test_guarded_sync_is_only_a_note_without_options():
    assert kinds(analyse(units(GUARDED_A, GUARDED_B))) == [(NOTE, LOOP)]


def test_real_loop_is_a_warning():
    a = {
        "triggers": [{"trigger": "state", "entity_id": "input_boolean.x", "to": "on"}],
        "actions": [
            {"action": "input_boolean.turn_on", "target": {"entity_id": "input_boolean.y"}}
        ],
    }
    b = {
        "triggers": [{"trigger": "state", "entity_id": "input_boolean.y", "to": "on"}],
        "actions": [
            {"action": "input_boolean.turn_on", "target": {"entity_id": "input_boolean.x"}}
        ],
    }
    findings = analyse(units(a, b))
    assert kinds(findings) == [(WARNING, LOOP)]


def test_loop_through_script_is_found():
    a = {
        "triggers": [{"trigger": "state", "entity_id": "input_boolean.x", "to": "on"}],
        "actions": [{"action": "script.kette"}],
    }
    script = {
        "sequence": [{"action": "input_boolean.toggle", "target": {"entity_id": "input_boolean.x"}}]
    }
    findings = analyse(units(a, scripts=[("kette", script)]))
    assert kinds(findings) == [(NOTE, SELF_TRIGGER)]
    assert "(kette)" in findings[0].lines[0]


def test_race_on_shared_time_trigger():
    open_cover = {
        "alias": "Auf",
        "triggers": [{"trigger": "time", "at": "07:00:00"}],
        "actions": [{"action": "cover.open_cover", "target": {"entity_id": "cover.wz"}}],
    }
    close_cover = {
        "alias": "Zu",
        "triggers": [{"trigger": "time", "at": "07:00:00"}],
        "actions": [{"action": "cover.close_cover", "target": {"entity_id": "cover.wz"}}],
    }
    assert kinds(analyse(units(open_cover, close_cover))) == [(WARNING, CONFLICT)]


def test_race_excluded_by_conditions_and_trigger_values():
    away = {
        "triggers": [{"trigger": "state", "entity_id": "input_select.haus_modus"}],
        "conditions": [
            {"condition": "state", "entity_id": "input_select.haus_modus", "state": "Abwesend"}
        ],
        "actions": [{"action": "light.turn_off", "target": {"entity_id": "light.flur"}}],
    }
    home = {
        "triggers": [{"trigger": "state", "entity_id": "input_select.haus_modus", "to": "Zuhause"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.flur"}}],
    }
    assert analyse(units(away, home), MODES) == []


def test_from_trigger_excludes_old_state():
    cinema = {
        "triggers": [{"trigger": "state", "entity_id": "input_select.haus_modus"}],
        "actions": [
            {
                "choose": [
                    {
                        "conditions": [
                            {
                                "condition": "state",
                                "entity_id": "input_select.haus_modus",
                                "state": "Kino",
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
    leave = {
        "triggers": [{"trigger": "state", "entity_id": "input_select.haus_modus", "from": "Kino"}],
        "actions": [{"action": "cover.open_cover", "target": {"entity_id": "cover.wz"}}],
    }
    assert analyse(units(cinema, leave), MODES) == []


def test_delayed_or_different_duration_is_not_simultaneous():
    on = {
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.p", "to": "on"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.bad"}}],
    }
    later = {
        "triggers": [
            {"trigger": "state", "entity_id": "binary_sensor.p", "to": "on", "for": {"minutes": 20}}
        ],
        "actions": [{"action": "light.turn_off", "target": {"entity_id": "light.bad"}}],
    }
    waiting = {
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.p", "to": "on"}],
        "actions": [
            {"delay": {"seconds": 30}},
            {"action": "light.turn_off", "target": {"entity_id": "light.bad"}},
        ],
    }
    assert analyse(units(on, later)) == []
    assert analyse(units(on, waiting)) == []


def test_unknown_trigger_id_is_an_error():
    config = {
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.p", "to": "on", "id": "an"}],
        "actions": [
            {
                "choose": [
                    {
                        "conditions": [{"condition": "trigger", "id": "aus"}],
                        "sequence": [
                            {"action": "light.turn_off", "target": {"entity_id": "light.a"}}
                        ],
                    }
                ]
            }
        ],
    }
    findings = analyse(units(config))
    assert (ERROR, UNKNOWN_TRIGGER_ID) in kinds(findings)
    assert (NOTE, IDLE_TRIGGER) in kinds(findings)  # Auslöser „an“ bewirkt nichts


def test_trigger_entity_template_is_resolved():
    config = {
        "triggers": [{"trigger": "state", "entity_id": ["light.a", "light.b"]}],
        "actions": [{"action": "light.toggle", "target": {"entity_id": "{{ trigger.entity_id }}"}}],
    }
    unit = units(config)[0]
    assert {w.entity for w in unit.writes} == {"light.a", "light.b"}
    assert kinds(analyse([unit])) == [(NOTE, SELF_TRIGGER)]


def test_reconnect_trigger_is_not_hit_by_writes():
    config = {
        "triggers": [{"trigger": "state", "entity_id": "cover.sz", "from": "unavailable"}],
        "actions": [{"action": "cover.close_cover", "target": {"entity_id": "cover.sz"}}],
    }
    assert analyse(units(config)) == []


def test_blueprints_are_skipped():
    unit = build_unit("automation.bp", "BP", "automation", {"use_blueprint": {"path": "x.yaml"}})
    assert unit.uses_blueprint == "x.yaml"
    assert analyse([unit]) == []
