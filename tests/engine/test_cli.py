"""Tests für die Kommandozeile (Analyse eines JSON-Exports)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from custom_components.automationscheck.engine import cli
from custom_components.automationscheck.engine.analysis import NOTE, Finding

ENGINE_PARENT = Path(cli.__file__).resolve().parents[1]

MODUS_A = {
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
MODUS_B = {
    "id": "kino_aus",
    "alias": "Kino verlassen",
    "triggers": [{"trigger": "state", "entity_id": "input_select.haus_modus", "from": "Kino"}],
    "actions": [{"action": "cover.open_cover", "target": {"entity_id": "cover.wz"}}],
}
BROKEN_ID = {
    "id": "licht_bad",
    "alias": "Licht Bad",
    "triggers": [
        {"trigger": "state", "entity_id": "binary_sensor.bad", "to": "on", "id": "da"},
        {"trigger": "state", "entity_id": "binary_sensor.bad", "to": "off", "id": "aus"},
    ],
    "actions": [
        {
            "choose": [
                {
                    "conditions": [{"condition": "trigger", "id": "da"}],
                    "sequence": [{"action": "light.turn_on", "target": {"entity_id": "light.bad"}}],
                },
                {
                    "conditions": [{"condition": "trigger", "id": "weg"}],
                    "sequence": [
                        {"action": "light.turn_off", "target": {"entity_id": "light.bad"}}
                    ],
                },
            ]
        }
    ],
}
QUIET = {
    "id": "ruhig",
    "alias": "Ruhige Automation",
    "triggers": [{"trigger": "state", "entity_id": "binary_sensor.tuer", "to": "on"}],
    "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.flur"}}],
}
OPTIONS = {"input_select.haus_modus": ["Zuhause", "Abwesend", "Kino", "Urlaub"]}


def write(tmp_path: Path, name: str, data: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_units_accepts_lists_and_dicts_and_fills_names(tmp_path):
    export = write(
        tmp_path,
        "export.json",
        {
            "automations": [
                {"entity_id": "automation.a", "friendly_name": "Name aus HA", "config": QUIET},
                {"config": {**QUIET, "id": "ohne_entity", "alias": "Nur Alias"}},
            ],
            "scripts": {
                "script.licht": {
                    "entity_id": "script.licht",
                    "config": {"sequence": [{"action": "light.turn_off"}]},
                }
            },
        },
    )

    units = {unit.entity_id: unit for unit in cli.load_units([export])}

    assert set(units) == {"automation.a", "automation.ohne_entity", "script.licht"}
    assert units["automation.a"].name == "Name aus HA"
    assert units["automation.ohne_entity"].name == "Nur Alias"
    assert units["script.licht"].name == "script.licht"
    assert units["script.licht"].kind == "script"


def test_later_exports_replace_earlier_entries(tmp_path):
    first = write(
        tmp_path, "alt.json", {"automations": [{"entity_id": "automation.x", "config": QUIET}]}
    )
    second = write(
        tmp_path,
        "neu.json",
        {"automations": [{"entity_id": "automation.x", "friendly_name": "Neu", "config": QUIET}]},
    )

    units = cli.load_units([first, second])

    assert [(u.entity_id, u.name) for u in units] == [("automation.x", "Neu")]


def test_load_domains(tmp_path):
    assert cli.load_domains(None) == {}
    path = write(tmp_path, "optionen.json", {"input_select.stufe": [1, "2"]})
    assert cli.load_domains(path) == {"input_select.stufe": frozenset({"1", "2"})}


LOOP_X = {
    "alias": "X schaltet Y",
    "triggers": [{"trigger": "state", "entity_id": "input_boolean.x", "to": "on"}],
    "actions": [{"action": "input_boolean.turn_on", "target": {"entity_id": "input_boolean.y"}}],
}
LOOP_Y = {
    "alias": "Y schaltet X",
    "triggers": [{"trigger": "state", "entity_id": "input_boolean.y", "to": "on"}],
    "actions": [{"action": "input_boolean.turn_on", "target": {"entity_id": "input_boolean.x"}}],
}
TOGGLE = {
    "alias": "Umschalter",
    "triggers": [{"trigger": "state", "entity_id": "light.flur"}],
    "actions": [{"action": "light.toggle", "target": {"entity_id": "{{ trigger.entity_id }}"}}],
}


def busy_writer(number: int, action: str) -> dict:
    return {
        "alias": f"Koch {number}",
        "triggers": [{"trigger": "time", "at": f"0{number}:00:00"}],
        "actions": [{"action": f"light.{action}", "target": {"entity_id": "light.kueche"}}],
    }


def export(tmp_path: Path, *configs: dict) -> Path:
    automations = [
        {"entity_id": f"automation.a{index}", "config": config}
        for index, config in enumerate(configs)
    ]
    return write(tmp_path, "export.json", {"automations": automations})


def test_render_without_findings(tmp_path):
    report = cli.render(cli.load_units([export(tmp_path, QUIET)]))
    assert report.splitlines() == [
        "# Automationscheck – 1 Automationen/Skripte",
        "",
        "Keine Auffälligkeiten gefunden.",
    ]


def test_render_conflict_unknown_trigger_id_and_idle_trigger(tmp_path):
    path = export(tmp_path, MODUS_A, MODUS_B, BROKEN_ID)
    report = cli.render(
        cli.load_units([path]), cli.load_domains(write(tmp_path, "o.json", OPTIONS))
    )

    assert report.startswith("# Automationscheck – 3 Automationen/Skripte\n")
    assert "## Fehler: „Licht Bad“ prüft Trigger-IDs, die es nicht gibt" in report
    assert "- unbekannt: weg" in report
    assert "- vorhanden: aus, da" in report
    assert (
        "## Warnung: „Modus - Aktivierung“ und „Kino verlassen“ stellen `cover.wz` "
        "gleichzeitig unterschiedlich"
    ) in report
    assert "- gemeinsamer Auslöser: `input_select.haus_modus`" in report
    assert "- Situation: `input_select.haus_modus` = Urlaub" in report
    assert "## Hinweis: „Licht Bad“ hat Auslöser ohne Wirkung" in report
    assert "- betroffen: aus" in report
    # Fehler stehen vor Warnungen, Warnungen vor Hinweisen.
    assert report.index("## Fehler") < report.index("## Warnung") < report.index("## Hinweis")


def test_render_loop_self_trigger_and_busy_entities(tmp_path):
    configs = [LOOP_X, LOOP_Y, TOGGLE]
    configs += [busy_writer(n, "turn_on" if n % 2 else "turn_off") for n in range(1, 5)]
    report = cli.render(cli.load_units([export(tmp_path, *configs)]))

    assert "## Warnung: Rückkopplung zwischen" in report
    assert "„Umschalter“ kann sich selbst erneut auslösen" in report
    assert "## Viele Köche" in report
    assert "- `light.kueche`: 4 Automationen (Koch 1, Koch 2, Koch 3, Koch 4)" in report


def test_unknown_finding_kind_falls_back_to_its_name():
    finding = Finding(severity=NOTE, kind="neu", units=["automation.a"], lines=["x"])
    assert cli.title(finding) == "neu"
    assert cli.details(finding) == ["x"]


def test_main_prints_report(tmp_path, capsys):
    path = export(tmp_path, MODUS_A, MODUS_B)
    options = write(tmp_path, "optionen.json", OPTIONS)

    assert cli.main([str(path), "--options", str(options)]) == 0

    out = capsys.readouterr().out
    assert "stellen `cover.wz` gleichzeitig unterschiedlich" in out


def test_documented_command_line_call(tmp_path):
    """Der Aufruf aus der README: python -m engine.cli export.json --options optionen.json."""
    path = export(tmp_path, MODUS_A, MODUS_B, BROKEN_ID)
    options = write(tmp_path, "optionen.json", OPTIONS)

    result = subprocess.run(
        [sys.executable, "-m", "engine.cli", str(path), "--options", str(options)],
        cwd=ENGINE_PARENT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("# Automationscheck – 3 Automationen/Skripte")
    assert "## Fehler: „Licht Bad“ prüft Trigger-IDs, die es nicht gibt" in result.stdout


def test_command_line_reports_unreadable_files_without_traceback(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "engine.cli", str(tmp_path / "fehlt.json")],
        cwd=ENGINE_PARENT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 2
    assert "Datei nicht lesbar" in result.stderr
    assert "fehlt.json" in result.stderr
    assert "Traceback" not in result.stderr


def test_main_reports_invalid_json(tmp_path, capsys):
    broken = tmp_path / "kaputt.json"
    broken.write_text("{nicht json", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        cli.main([str(broken)])
    assert exit_info.value.code == 2
    assert "Kein gültiges JSON" in capsys.readouterr().err
