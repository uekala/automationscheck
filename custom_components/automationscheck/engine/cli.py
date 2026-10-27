"""Kommandozeile: Analyse eines JSON-Exports von Automationen und Skripten.

Aufruf aus dem Ordner ``custom_components/automationscheck``::

    python -m engine.cli export.json [--options optionen.json]

``export.json`` enthält ``{"automations": [{"entity_id", "friendly_name",
"config"}], "scripts": [...]}``; ``optionen.json`` die Auswahlmöglichkeiten von
input_select-Helfern als ``{"input_select.x": ["A", "B"]}``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import (
    CONFLICT,
    ERROR,
    IDLE_TRIGGER,
    LOOP,
    SELF_TRIGGER,
    UNKNOWN_TRIGGER_ID,
    WARNING,
    Finding,
    analyse,
    busy_entities,
)
from .model import Unit, build_unit, inline_scripts

SEVERITY = {ERROR: "Fehler", WARNING: "Warnung"}


def load_units(paths: list[Path]) -> list[Unit]:
    units: dict[str, Unit] = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for kind in ("automations", "scripts"):
            entries = data.get(kind) or {}
            items = entries.values() if isinstance(entries, dict) else entries
            for entry in items:
                config = entry.get("config") or {}
                entity_id = entry.get("entity_id") or f"{kind[:-1]}.{config.get('id')}"
                name = entry.get("friendly_name") or config.get("alias") or entity_id
                units[entity_id] = build_unit(entity_id, name, kind[:-1], config)
    inline_scripts(units)
    return list(units.values())


def load_domains(path: Path | None) -> dict[str, frozenset[str]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {entity: frozenset(map(str, options)) for entity, options in data.items()}


def title(finding: Finding) -> str:
    p = finding.placeholders
    if finding.kind == CONFLICT:
        return (
            f"„{p['automation_a']}“ und „{p['automation_b']}“ stellen `{p['entity']}` "
            "gleichzeitig unterschiedlich"
        )
    if finding.kind == LOOP:
        return f"Rückkopplung zwischen {p['automations']}"
    if finding.kind == SELF_TRIGGER:
        return f"„{p['automation']}“ kann sich selbst erneut auslösen"
    if finding.kind == UNKNOWN_TRIGGER_ID:
        return f"„{p['automation']}“ prüft Trigger-IDs, die es nicht gibt"
    if finding.kind == IDLE_TRIGGER:
        return f"„{p['automation']}“ hat Auslöser ohne Wirkung"
    return finding.kind


def details(finding: Finding) -> list[str]:
    p = finding.placeholders
    if finding.kind == CONFLICT:
        return [
            f"gemeinsamer Auslöser: {p['trigger']}",
            f"Situation: {p['situation']}",
            f"„{p['automation_a']}“: {p['value_a']}",
            f"„{p['automation_b']}“: {p['value_b']}",
        ]
    if finding.kind == UNKNOWN_TRIGGER_ID:
        return [f"unbekannt: {p['ids']}", f"vorhanden: {p['defined']}"]
    if finding.kind == IDLE_TRIGGER:
        return [f"betroffen: {p['ids']}"]
    return finding.lines


def render(units: list[Unit], domains: dict[str, frozenset[str]] | None = None) -> str:
    findings = analyse(units, domains)
    lines = [f"# Automationscheck – {len(units)} Automationen/Skripte", ""]
    if not findings:
        lines.append("Keine Auffälligkeiten gefunden.")
    for finding in findings:
        lines.append(f"## {SEVERITY.get(finding.severity, 'Hinweis')}: {title(finding)}")
        lines.extend(f"- {d}" for d in details(finding))
        lines.append("")
    busy = busy_entities(units)
    if busy:
        lines.append("## Viele Köche")
        for entity, owners in busy:
            lines.append(f"- `{entity}`: {len(owners)} Automationen ({', '.join(owners)})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Findet Schleifen und Gegenspieler in HA-Automationen"
    )
    parser.add_argument("exports", nargs="+", type=Path)
    parser.add_argument("--options", type=Path, help="JSON {entity_id: [Optionen]}")
    args = parser.parse_args(argv)
    print(render(load_units(args.exports), load_domains(args.options)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
