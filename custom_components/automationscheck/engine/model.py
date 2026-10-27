"""Zerlegt Automationen und Skripte in Auslöser und Schreibzugriffe.

Ein Schreibzugriff ("Write") beschreibt, welche Entität eine Aktion verändert,
welches Merkmal (Zustand, Position, Temperatur …) und – wenn statisch
erkennbar – auf welchen Wert. Dazu merkt er sich alle Bedingungen auf dem Weg
dorthin, damit sich später gegenseitig ausschließende Pfade erkennen lassen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

UNKNOWN = "?"  # Wert nicht statisch bestimmbar (Template, Toggle …)
TRIGGER_ENTITY = "@trigger"  # Ziel ist die auslösende Entität ({{ trigger.entity_id }})
ANY_CHANGE = "*"  # Wert ändert sich sicher, aber beliebig (Zeitstempel, Zähler)

_TEMPLATE = re.compile(r"\{\{|\{%")
_ENTITY_IN_TEMPLATE = re.compile(
    r"(?:states|is_state|state_attr|is_state_attr|has_value)\(\s*['\"]([a-z_]+\.[a-z0-9_]+)['\"]"
    r"|states\.([a-z_]+)\.([a-z0-9_]+)"
)

_ON_OFF = {"turn_on": "on", "turn_off": "off", "toggle": UNKNOWN}
_SIMPLE_DOMAINS = {
    "light",
    "switch",
    "fan",
    "input_boolean",
    "media_player",
    "siren",
    "humidifier",
    "remote",
    "automation",
    "climate",
    "water_heater",
    "vacuum",
}
_ALARM = {
    "alarm_arm_away": "armed_away",
    "alarm_arm_home": "armed_home",
    "alarm_arm_night": "armed_night",
    "alarm_arm_vacation": "armed_vacation",
    "alarm_arm_custom_bypass": "armed_custom_bypass",
    "alarm_disarm": "disarmed",
    "alarm_trigger": "triggered",
}
_NO_STATE_DOMAINS = {
    "notify",
    "persistent_notification",
    "tts",
    "logger",
    "system_log",
    "conversation",
    "camera",
    "recorder",
    "frontend",
    "browser_mod",
    "shell_command",
    "rest_command",
    "update",
    "button",
    "input_button",
}


def is_template(value: Any) -> bool:
    return isinstance(value, str) and bool(_TEMPLATE.search(value))


def as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def template_entities(text: str) -> set[str]:
    found: set[str] = set()
    for match in _ENTITY_IN_TEMPLATE.finditer(text):
        if match.group(1):
            found.add(match.group(1))
        elif match.group(2):
            found.add(f"{match.group(2)}.{match.group(3)}")
    return found


@dataclass(frozen=True)
class Trigger:
    """Ein Auslöser, soweit er an eine Entität gebunden ist oder ein festes Ereignis hat."""

    kind: str  # state | numeric_state | template | time | sun | event | other
    entity: str | None = None
    attribute: str | None = None
    to: tuple[str, ...] = ()
    from_: tuple[str, ...] = ()
    not_to: tuple[str, ...] = ()
    above: float | None = None
    below: float | None = None
    key: str | None = None  # für Zeit/Sonne/Ereignis: Vergleichsschlüssel
    trigger_id: str | None = None
    for_key: str | None = None  # Verzögerung (`for:`), normalisiert


@dataclass
class Write:
    """Eine Aktion, die eine Entität verändert."""

    entity: str
    feature: str  # state | position | temperature | brightness | preset_mode …
    value: str  # statischer Wert, UNKNOWN oder ANY_CHANGE
    service: str
    path_conditions: list[dict] = field(default_factory=list)
    trigger_ids: frozenset[str] | None = None  # None = alle Auslöser
    via: str | None = None  # Skript, über das geschrieben wird
    delayed: bool = False  # erst nach delay/wait – nicht gleichzeitig mit dem Auslöser


@dataclass
class Unit:
    """Eine Automation oder ein Skript."""

    entity_id: str
    name: str
    kind: str  # automation | script
    triggers: list[Trigger] = field(default_factory=list)
    writes: list[Write] = field(default_factory=list)
    calls: list[tuple[str, list[dict], frozenset[str] | None]] = field(default_factory=list)
    conditions: list[dict] = field(default_factory=list)
    referenced_trigger_ids: set[str] = field(default_factory=set)
    unresolved_targets: list[str] = field(default_factory=list)
    uses_blueprint: str | None = None
    mode: str = "single"
    trigger_guarded_choose: bool = False
    has_default_path: bool = True


def _fmt_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else str(number)


# --------------------------------------------------------------------------
# Auslöser
# --------------------------------------------------------------------------


def _str_tuple(value: Any) -> tuple[str, ...]:
    return tuple(str(v) for v in as_list(value) if v is not None and not is_template(v))


def _duration_key(value: Any) -> str | None:
    """Normalisiert eine Dauer (`for:`) zu Sekunden, damit sie vergleichbar wird."""
    if not value:
        return None
    if isinstance(value, dict):
        seconds = (
            float(value.get("days", 0)) * 86400
            + float(value.get("hours", 0)) * 3600
            + float(value.get("minutes", 0)) * 60
            + float(value.get("seconds", 0))
        )
        return f"{seconds:g}"
    if isinstance(value, str) and not is_template(value):
        parts = [float(p) for p in value.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0.0)
        return f"{parts[-3] * 3600 + parts[-2] * 60 + parts[-1]:g}"
    return str(value)


def parse_triggers(raw_triggers: Any) -> list[Trigger]:
    result: list[Trigger] = []
    for raw in as_list(raw_triggers):
        if not isinstance(raw, dict):
            continue
        kind = raw.get("trigger") or raw.get("platform") or "other"
        trigger_id = str(raw["id"]) if raw.get("id") is not None else None
        entities = [e for e in as_list(raw.get("entity_id")) if isinstance(e, str)]
        target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
        entities += [e for e in as_list(target.get("entity_id")) if isinstance(e, str)]
        for_key = _duration_key(raw.get("for"))
        if kind == "state":
            for entity in entities:
                result.append(
                    Trigger(
                        "state",
                        entity,
                        raw.get("attribute"),
                        _str_tuple(raw.get("to")),
                        from_=_str_tuple(raw.get("from")),
                        not_to=_str_tuple(raw.get("not_to")),
                        trigger_id=trigger_id,
                        for_key=for_key,
                    )
                )
        elif kind == "numeric_state":
            for entity in entities:
                above = raw.get("above")
                below = raw.get("below")
                result.append(
                    Trigger(
                        "numeric_state",
                        entity,
                        raw.get("attribute"),
                        above=float(above) if isinstance(above, (int, float)) else None,
                        below=float(below) if isinstance(below, (int, float)) else None,
                        trigger_id=trigger_id,
                        for_key=for_key,
                    )
                )
        elif kind == "template":
            for entity in template_entities(str(raw.get("value_template", ""))):
                result.append(Trigger("template", entity, trigger_id=trigger_id))
        elif kind == "time":
            for at in as_list(raw.get("at")):
                result.append(Trigger("time", key=str(at), trigger_id=trigger_id))
        elif kind == "sun":
            key = f"{raw.get('event')}{raw.get('offset') or ''}"
            result.append(Trigger("sun", key=key, trigger_id=trigger_id))
        elif kind == "homeassistant":
            result.append(Trigger("event", key=f"ha_{raw.get('event')}", trigger_id=trigger_id))
        elif kind == "event":
            result.append(Trigger("event", key=str(raw.get("event_type")), trigger_id=trigger_id))
        elif "." in kind and entities:
            # Zweckgebundene Auslöser (z. B. motion.detected) mit Ziel-Entitäten
            for entity in entities:
                result.append(Trigger("state", entity, trigger_id=trigger_id, key=kind))
        else:
            key = kind if kind in ("time_pattern", "webhook", "mqtt", "device", "zone") else None
            result.append(Trigger("other", key=key, trigger_id=trigger_id))
    return result


# --------------------------------------------------------------------------
# Aktionen
# --------------------------------------------------------------------------


def _targets(action: dict) -> tuple[list[str], list[str]]:
    """Ziel-Entitäten und nicht auflösbare Ziele (Bereich, Gerät, Label, Template)."""
    entities: list[str] = []
    unresolved: list[str] = []
    sources = [action.get("target"), action.get("data"), action]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for value in as_list(source.get("entity_id")):
            if not isinstance(value, str):
                continue
            if is_template(value):
                if re.fullmatch(r"\{\{\s*trigger\.entity_id\s*\}\}", value.strip()):
                    entities.append(TRIGGER_ENTITY)
                else:
                    unresolved.append("template")
            elif value == "all":
                entities.append("all")
            else:
                entities.extend(part.strip() for part in value.split(",") if part.strip())
        for key in ("area_id", "device_id", "floor_id", "label_id"):
            if source is not action and source.get(key):
                unresolved.append(key)
    return list(dict.fromkeys(entities)), unresolved


def _value(data: dict, key: str) -> str:
    value = data.get(key)
    if value is None:
        return UNKNOWN
    if is_template(value):
        return UNKNOWN
    return _fmt_number(value)


def effects(service: str, entity: str, data: dict) -> list[tuple[str, str]]:
    """Welche Merkmale eine Dienstaktion an einer Entität verändert."""
    domain, _, name = service.partition(".")
    entity_domain = entity.split(".", 1)[0]
    if domain == "homeassistant" and name in _ON_OFF:
        return [("state", _ON_OFF[name])]
    if domain in _SIMPLE_DOMAINS and name in _ON_OFF:
        result = [("state", _ON_OFF[name])]
        if (
            domain == "light"
            and name == "turn_on"
            and ("brightness_pct" in data or "brightness" in data)
        ):
            result.append(("brightness", UNKNOWN))
        return result
    if domain == "cover":
        if name == "open_cover":
            return [("position", "100")]
        if name == "close_cover":
            return [("position", "0")]
        if name == "set_cover_position":
            return [("position", _value(data, "position"))]
        if name == "toggle":
            return [("position", UNKNOWN)]
        if name in ("set_cover_tilt_position", "open_cover_tilt", "close_cover_tilt"):
            return [("tilt", UNKNOWN)]
        return []
    if domain == "climate":
        if name == "set_temperature":
            result = [("temperature", _value(data, "temperature"))]
            if "hvac_mode" in data:
                result.append(("state", _value(data, "hvac_mode")))
            return result
        if name == "set_hvac_mode":
            return [("state", _value(data, "hvac_mode"))]
        if name == "set_preset_mode":
            return [("preset_mode", _value(data, "preset_mode"))]
        return []
    if domain in ("input_select", "select") and name == "select_option":
        return [("state", _value(data, "option"))]
    if domain in ("input_select", "select") and name.startswith("select_"):
        return [("state", UNKNOWN)]
    if domain in ("input_number", "number", "input_text", "text") and name == "set_value":
        return [("state", _value(data, "value"))]
    if domain in ("input_number", "counter") and name in ("increment", "decrement", "reset"):
        return [("state", ANY_CHANGE)]
    if domain in ("input_datetime", "datetime", "date", "time") and name.startswith("set_"):
        return [("state", ANY_CHANGE)]
    if domain == "lock" and name in ("lock", "unlock", "open"):
        return [("state", {"lock": "locked", "unlock": "unlocked", "open": "open"}[name])]
    if domain == "alarm_control_panel" and name in _ALARM:
        return [("state", _ALARM[name])]
    if domain == "timer":
        mapping = {"start": "active", "cancel": "idle", "finish": "idle", "pause": "paused"}
        return [("state", mapping.get(name, UNKNOWN))]
    if domain == "valve":
        mapping = {"open_valve": "open", "close_valve": "closed"}
        return [("state", mapping.get(name, UNKNOWN))]
    if name in _ON_OFF and entity_domain not in _NO_STATE_DOMAINS:
        return [("state", _ON_OFF[name])]
    return []


def _negate(conds: list[dict]) -> dict | None:
    """Verneinung einer Bedingungsliste – nur wenn sie aus genau einer Zustandsbedingung besteht."""
    plain = [c for c in conds if c.get("condition") != "trigger"]
    if len(plain) == 1 and len(conds) == 1 and plain[0].get("condition") == "state":
        return {"condition": "not", "conditions": [plain[0]]}
    return None


def _trigger_ids_of(cond: Any) -> frozenset[str] | None:
    """Trigger-IDs, auf die eine Bedingung einschränkt (None = keine Einschränkung).

    Versteht `condition: trigger`, ein `or` aus lauter Trigger-Bedingungen und
    ein `and`, das eine Trigger-Bedingung enthält.
    """
    if not isinstance(cond, dict):
        return None
    kind = cond.get("condition")
    if kind == "trigger":
        return frozenset(str(i) for i in as_list(cond.get("id")))
    nested = [_trigger_ids_of(c) for c in as_list(cond.get("conditions"))]
    if kind == "or" and nested and all(n is not None for n in nested):
        return frozenset().union(*nested)
    if kind == "and":
        result: frozenset[str] | None = None
        for ids in nested:
            if ids is not None:
                result = ids if result is None else result & ids
        return result
    return None


class _Walker:
    def __init__(self, unit: Unit) -> None:
        self.unit = unit

    def walk(
        self,
        actions: Any,
        conditions: list[dict],
        trigger_ids: frozenset[str] | None,
        delayed: bool = False,
    ) -> bool:
        """Läuft eine Aktionsfolge ab; liefert, ob danach Zeit vergangen ist."""
        for action in as_list(actions):
            if isinstance(action, dict) and action.get("enabled") is not False:
                delayed = self._action(action, conditions, trigger_ids, delayed)
        return delayed

    def _note_conditions(self, conds: list[dict]) -> None:
        for cond in conds:
            if isinstance(cond, dict) and cond.get("condition") == "trigger":
                self.unit.referenced_trigger_ids.update(str(i) for i in as_list(cond.get("id")))
            for nested in ("conditions",):
                if isinstance(cond, dict) and isinstance(cond.get(nested), list):
                    self._note_conditions(cond[nested])

    def _narrow(
        self, conds: list[dict], trigger_ids: frozenset[str] | None
    ) -> frozenset[str] | None:
        for cond in conds:
            ids = _trigger_ids_of(cond)
            if ids is not None:
                trigger_ids = ids if trigger_ids is None else trigger_ids & ids
        return trigger_ids

    def _action(
        self,
        action: dict,
        conditions: list[dict],
        trigger_ids: frozenset[str] | None,
        delayed: bool,
    ) -> bool:
        if "choose" in action:
            after = delayed
            earlier: list[dict] = []  # Verneinungen vorheriger Zweige (erster Treffer gewinnt)
            for option in as_list(action.get("choose")):
                if not isinstance(option, dict):
                    continue
                conds = [c for c in as_list(option.get("conditions")) if isinstance(c, dict)]
                self._note_conditions(conds)
                after |= self.walk(
                    option.get("sequence"),
                    conditions + earlier + conds,
                    self._narrow(conds, trigger_ids),
                    delayed,
                )
                if negated := _negate(conds):
                    earlier.append(negated)
            if action.get("default"):
                after |= self.walk(
                    action.get("default"), conditions + earlier, trigger_ids, delayed
                )
            return after
        if "if" in action:
            conds = [c for c in as_list(action.get("if")) if isinstance(c, dict)]
            self._note_conditions(conds)
            after = self.walk(
                action.get("then"), conditions + conds, self._narrow(conds, trigger_ids), delayed
            )
            negated = _negate(conds)
            else_conds = conditions + ([negated] if negated else [])
            return self.walk(action.get("else"), else_conds, trigger_ids, delayed) or after
        if "parallel" in action:
            after = delayed
            for item in as_list(action["parallel"]):
                after |= self.walk(item, conditions, trigger_ids, delayed)
            return after
        if "sequence" in action:
            return self.walk(action["sequence"], conditions, trigger_ids, delayed)
        if "repeat" in action and isinstance(action["repeat"], dict):
            self.walk(action["repeat"].get("sequence"), conditions, trigger_ids, True)
            return True
        if any(k in action for k in ("delay", "wait_template", "wait_for_trigger")):
            return True
        if "condition" in action:
            # Bedingung als Aktion: gilt für alle folgenden Schritte – wird
            # vereinfachend nur vermerkt
            self._note_conditions([action])
            return delayed
        service = action.get("action") or action.get("service")
        if not isinstance(service, str) or is_template(service):
            return delayed
        data = action.get("data") if isinstance(action.get("data"), dict) else {}
        domain, _, name = service.partition(".")
        if domain == "script" and name not in ("turn_on", "turn_off", "toggle", "reload"):
            self.unit.calls.append((f"script.{name}", conditions, trigger_ids))
            return delayed
        entities, unresolved = _targets(action)
        if domain == "script" and name == "turn_on":
            for entity in entities:
                self.unit.calls.append((entity, conditions, trigger_ids))
            return delayed
        if domain in _NO_STATE_DOMAINS:
            return delayed
        self.unit.unresolved_targets.extend(f"{service}:{u}" for u in unresolved)
        if TRIGGER_ENTITY in entities:
            entities = [e for e in entities if e != TRIGGER_ENTITY] + [
                t.entity
                for t in self.unit.triggers
                if t.entity and (trigger_ids is None or t.trigger_id in trigger_ids)
            ]
        for entity in dict.fromkeys(entities):
            for feature, value in effects(service, entity, data):
                self.unit.writes.append(
                    Write(
                        entity,
                        feature,
                        value,
                        service,
                        list(conditions),
                        trigger_ids,
                        delayed=delayed,
                    )
                )
        return delayed


def build_unit(entity_id: str, name: str, kind: str, config: dict) -> Unit:
    unit = Unit(entity_id=entity_id, name=name, kind=kind, mode=str(config.get("mode", "single")))
    if config.get("use_blueprint"):
        unit.uses_blueprint = str(config["use_blueprint"].get("path"))
        return unit
    unit.triggers = parse_triggers(config.get("triggers") or config.get("trigger"))
    unit.conditions = [
        c
        for c in as_list(config.get("conditions") or config.get("condition"))
        if isinstance(c, dict)
    ]
    walker = _Walker(unit)
    walker._note_conditions(unit.conditions)
    top_ids = walker._narrow(unit.conditions, None)
    actions = config.get("actions") or config.get("action") or config.get("sequence")
    walker.walk(actions, list(unit.conditions), top_ids)

    # Besteht die Aktion nur aus einem choose, dessen Zweige alle über
    # Trigger-IDs laufen und das keinen Standardzweig hat?
    top_actions = [a for a in as_list(actions) if isinstance(a, dict)]
    if len(top_actions) == 1 and "choose" in top_actions[0]:
        options = [o for o in as_list(top_actions[0]["choose"]) if isinstance(o, dict)]
        unit.trigger_guarded_choose = bool(options) and all(
            any(
                isinstance(c, dict) and c.get("condition") == "trigger"
                for c in as_list(o.get("conditions"))
            )
            for o in options
        )
        unit.has_default_path = bool(top_actions[0].get("default"))
    return unit


def inline_scripts(units: dict[str, Unit], depth: int = 3) -> None:
    """Überträgt Schreibzugriffe aufgerufener Skripte auf den Aufrufer."""
    for unit in units.values():
        seen: set[str] = set()
        queue = [(c, conds, ids, 1) for c, conds, ids in unit.calls]
        while queue:
            target, conds, ids, level = queue.pop(0)
            script = units.get(target)
            if script is None or target in seen or level > depth:
                continue
            seen.add(target)
            for write in script.writes:
                unit.writes.append(
                    Write(
                        write.entity,
                        write.feature,
                        write.value,
                        write.service,
                        conds + write.path_conditions,
                        ids,
                        via=script.name,
                        delayed=write.delayed,
                    )
                )
            queue.extend((c, conds + cc, ids, level + 1) for c, cc, _ in script.calls)
