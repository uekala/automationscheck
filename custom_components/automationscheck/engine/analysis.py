"""Prüfungen: Schleifen, Gegenspieler und Trigger-ID-Fehler.

Die Ergebnisse sind sprachneutral: Jeder Befund hat eine Art (``kind``), eine
Schwere und Platzhalter. Texte entstehen erst in der Oberfläche (Übersetzungen
der Reparaturen) bzw. in der Kommandozeile.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations

from .constraints import (
    Constraint,
    Constraints,
    domain_of,
    from_conditions,
    from_trigger,
    merge,
    satisfiable,
    without,
)
from .model import ANY_CHANGE, UNKNOWN, Trigger, Unit, Write

SURE = "sure"
MAYBE = "maybe"

ERROR = "error"
WARNING = "warning"
NOTE = "note"

UNKNOWN_TRIGGER_ID = "unknown_trigger_id"
IDLE_TRIGGER = "idle_trigger"
LOOP = "loop"
SELF_TRIGGER = "self_trigger"
CONFLICT = "conflict"

_FEATURE_FOR_ATTRIBUTE = {
    None: {"state"},
    "current_position": {"position"},
    "position": {"position"},
    "temperature": {"temperature"},
    "brightness": {"brightness"},
    "preset_mode": {"preset_mode"},
    "hvac_mode": {"state"},
}


@dataclass
class Finding:
    """Ein Befund, sprachneutral beschrieben."""

    severity: str
    kind: str
    units: list[str]
    placeholders: dict[str, str] = field(default_factory=dict)
    entity: str | None = None
    lines: list[str] = field(default_factory=list)  # Einzelschritte (Kette, Gründe)

    @property
    def key(self) -> str:
        """Stabile Kennung, damit derselbe Befund dieselbe Reparatur bleibt."""
        raw = "|".join([self.kind, self.entity or "", *sorted(self.units)])
        return f"{self.kind}_{hashlib.sha1(raw.encode()).hexdigest()[:10]}"


@dataclass
class Edge:
    source: Unit
    target: Unit
    write: Write
    trigger: Trigger
    strength: str


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------


def _state_of(write: Write) -> str:
    """Übersetzt einen geschriebenen Wert in den zu erwartenden Zustand."""
    if write.value in (UNKNOWN, ANY_CHANGE):
        return write.value
    if write.feature == "position":
        try:
            return "closed" if float(write.value) == 0 else "open"
        except ValueError:
            return UNKNOWN
    return write.value


def _features(trigger: Trigger, entity: str) -> set[str]:
    features = set(_FEATURE_FOR_ATTRIBUTE.get(trigger.attribute, {f"attr:{trigger.attribute}"}))
    if trigger.attribute is None and entity.startswith("cover."):
        features.add("position")  # Positionsänderung ändert auch open/closed
    return features


def _entity_matches(write: Write, trigger: Trigger) -> bool:
    if trigger.entity is None:
        return False
    if write.entity == trigger.entity:
        return True
    if write.entity == "all":
        return trigger.entity.split(".", 1)[0] == write.service.split(".", 1)[0]
    return False


def hits(write: Write, trigger: Trigger) -> str | None:
    """Kann dieser Schreibzugriff den Auslöser feuern lassen?"""
    if not _entity_matches(write, trigger):
        return None
    if trigger.kind == "template":
        return MAYBE
    if trigger.kind not in ("state", "numeric_state"):
        return None
    if write.feature not in _features(trigger, trigger.entity or ""):
        return None
    if trigger.kind == "state":
        if trigger.from_ and set(trigger.from_) <= {"unavailable", "unknown"}:
            return None  # feuert nur, wenn das Gerät wieder erreichbar wird
        if not trigger.to:
            return MAYBE
        state = _state_of(write)
        if state in (UNKNOWN, ANY_CHANGE):
            return MAYBE
        return SURE if state in trigger.to else None
    try:
        value = float(write.value)
    except ValueError:
        return MAYBE
    if trigger.above is not None and not value > trigger.above:
        return None
    if trigger.below is not None and not value < trigger.below:
        return None
    return SURE


def _written(write: Write) -> Constraints:
    state = _state_of(write)
    if state in (UNKNOWN, ANY_CHANGE) or write.feature not in ("state", "position"):
        return {}
    return {write.entity: Constraint(allowed=frozenset({state}))}


def _writes_for_trigger(unit: Unit, trigger: Trigger) -> list[Write]:
    return [
        w
        for w in unit.writes
        if w.trigger_ids is None
        or (trigger.trigger_id is not None and trigger.trigger_id in w.trigger_ids)
    ]


def _edge_possible(
    write: Write, trigger: Trigger, target: Unit, domains: dict[str, frozenset[str]]
) -> bool:
    """Kann der Ziel-Pfad nach diesem Schreibzugriff tatsächlich laufen?

    Bedingungen der Quelle gelten weiter (außer für die geschriebene Entität,
    die jetzt den neuen Wert hat) und müssen mit den Bedingungen des Ziels
    gemeinsam erfüllbar sein.
    """
    before = without(from_conditions(write.path_conditions), write.entity)
    for target_write in _writes_for_trigger(target, trigger):
        combined = merge(
            before,
            _written(write),
            from_conditions(target_write.path_conditions),
            from_trigger(trigger),
        )
        if satisfiable(combined, domains):
            return True
    return False


# --------------------------------------------------------------------------
# Sprachneutrale Kurzbeschreibungen
# --------------------------------------------------------------------------


def describe_value(write: Write) -> str:
    value = {UNKNOWN: "?", ANY_CHANGE: "*"}.get(write.value, write.value)
    return value if write.feature == "state" else f"{write.feature} {value}"


def describe_write(write: Write) -> str:
    via = f" ({write.via})" if write.via else ""
    return f"`{write.entity}` = {describe_value(write)}{via}"


def describe_trigger(trigger: Trigger) -> str:
    if trigger.entity is None:
        return f"{trigger.kind} {trigger.key or ''}".strip()
    text = f"`{trigger.entity}`"
    if trigger.attribute:
        text += f" [{trigger.attribute}]"
    if trigger.from_ or trigger.to:
        text += f": {'/'.join(trigger.from_) or '*'} → {'/'.join(trigger.to) or '*'}"
    if trigger.above is not None:
        text += f" > {trigger.above:g}"
    if trigger.below is not None:
        text += f" < {trigger.below:g}"
    return text


def describe_situation(constraints: Constraints, domains: dict[str, frozenset[str]]) -> str:
    parts = []
    for entity, constraint in sorted(constraints.items()):
        allowed = constraint.allowed or domain_of(entity, domains)
        if allowed is None:
            if constraint.excluded:
                parts.append(f"`{entity}` ≠ {' / '.join(sorted(constraint.excluded))}")
            continue
        values = sorted(allowed - constraint.excluded)
        if values and (constraint.allowed is not None or constraint.excluded):
            parts.append(f"`{entity}` = {' / '.join(values)}")
    return ", ".join(parts)


# --------------------------------------------------------------------------
# Kanten und Schleifen
# --------------------------------------------------------------------------


def build_edges(units: list[Unit], domains: dict[str, frozenset[str]]) -> list[Edge]:
    edges: list[Edge] = []
    for source in units:
        for target in units:
            if target.kind != "automation" or not target.triggers:
                continue
            for write in source.writes:
                for trigger in target.triggers:
                    strength = hits(write, trigger)
                    if strength is None:
                        continue
                    if not _edge_possible(write, trigger, target, domains):
                        continue
                    edges.append(Edge(source, target, write, trigger, strength))
    return edges


def _strongly_connected(nodes: list[str], adjacency: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []
    counter = [0]

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in sorted(adjacency.get(node, ())):
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            result.append(component)

    for node in nodes:
        if node not in index:
            visit(node)
    return result


def find_loops(units: list[Unit], edges: list[Edge]) -> list[Finding]:
    by_id = {u.entity_id: u for u in units}
    adjacency: dict[str, set[str]] = defaultdict(set)
    edge_map: dict[tuple[str, str], list[Edge]] = defaultdict(list)
    for edge in edges:
        adjacency[edge.source.entity_id].add(edge.target.entity_id)
        edge_map[(edge.source.entity_id, edge.target.entity_id)].append(edge)

    findings: list[Finding] = []
    for component in _strongly_connected(sorted(by_id), adjacency):
        members = set(component)
        if len(component) == 1:
            node = component[0]
            loops = edge_map.get((node, node), [])
            if not loops:
                continue
            sure = [e for e in loops if e.strength == SURE]
            shown = (sure or loops)[:4]
            findings.append(
                Finding(
                    WARNING if sure else NOTE,
                    SELF_TRIGGER,
                    [node],
                    {"automation": by_id[node].name},
                    lines=[
                        f"{describe_write(e.write)} → {describe_trigger(e.trigger)}" for e in shown
                    ],
                )
            )
            continue
        lines = []
        sure_count = 0
        for (src, dst), group in sorted(edge_map.items()):
            if src in members and dst in members and src != dst:
                best = sorted(group, key=lambda e: e.strength != SURE)[0]
                sure_count += best.strength == SURE
                lines.append(
                    f"**{by_id[src].name}** → {describe_write(best.write)} → **{by_id[dst].name}**"
                )
        ordered = sorted(members)
        findings.append(
            Finding(
                WARNING if sure_count >= 2 else NOTE,
                LOOP,
                ordered,
                {"automations": " ↔ ".join(by_id[m].name for m in ordered)},
                lines=lines,
            )
        )
    return findings


# --------------------------------------------------------------------------
# Gegenspieler
# --------------------------------------------------------------------------


def _trigger_signature(trigger: Trigger) -> tuple | None:
    if trigger.kind in ("time", "sun", "event") and trigger.key:
        return (trigger.kind, trigger.key)
    if trigger.kind in ("state", "numeric_state") and trigger.entity:
        return ("entity", trigger.entity, trigger.attribute)
    return None


def _triggers_overlap(a: Trigger, b: Trigger) -> bool:
    sa, sb = _trigger_signature(a), _trigger_signature(b)
    if sa is None or sa != sb:
        return False
    if sa[0] != "entity":
        return True
    if a.for_key != b.for_key:
        return False  # unterschiedliche Verzögerung: feuern nicht gleichzeitig
    if a.kind == "state" and b.kind == "state" and a.to and b.to:
        return bool(set(a.to) & set(b.to))
    return True


def _triggers_of(unit: Unit, write: Write) -> list[Trigger]:
    if write.trigger_ids is None:
        return unit.triggers
    return [t for t in unit.triggers if t.trigger_id in write.trigger_ids]


def find_conflicts(units: list[Unit], domains: dict[str, frozenset[str]]) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple] = set()
    automations = [u for u in units if u.kind == "automation" and u.triggers]
    for ua, ub in combinations(automations, 2):
        for wa in ua.writes:
            for wb in ub.writes:
                if wa.entity != wb.entity or wa.feature != wb.feature:
                    continue
                if wa.delayed or wb.delayed or wa.value == wb.value:
                    continue
                if UNKNOWN in (wa.value, wb.value) or ANY_CHANGE in (wa.value, wb.value):
                    continue
                key = (ua.entity_id, ub.entity_id, wa.entity, wa.feature)
                if key in seen:
                    continue
                path = merge(
                    from_conditions(wa.path_conditions), from_conditions(wb.path_conditions)
                )
                shared = [
                    (ta, tb)
                    for ta in _triggers_of(ua, wa)
                    for tb in _triggers_of(ub, wb)
                    if _triggers_overlap(ta, tb)
                    and satisfiable(merge(path, from_trigger(ta), from_trigger(tb)), domains)
                ]
                if not shared:
                    continue
                seen.add(key)
                ta, tb = shared[0]
                situation = describe_situation(
                    merge(path, from_trigger(ta), from_trigger(tb)), domains
                )
                findings.append(
                    Finding(
                        WARNING,
                        CONFLICT,
                        [ua.entity_id, ub.entity_id],
                        {
                            "automation_a": ua.name,
                            "automation_b": ub.name,
                            "entity": wa.entity,
                            "value_a": describe_value(wa),
                            "value_b": describe_value(wb),
                            "trigger": describe_trigger(ta),
                            "situation": situation or "–",
                        },
                        entity=wa.entity,
                    )
                )
    return findings


# --------------------------------------------------------------------------
# Trigger-IDs
# --------------------------------------------------------------------------


def find_trigger_id_issues(units: list[Unit]) -> list[Finding]:
    findings: list[Finding] = []
    for unit in units:
        if unit.kind != "automation" or not unit.triggers:
            continue
        defined = {t.trigger_id for t in unit.triggers if t.trigger_id is not None}
        unknown = sorted(unit.referenced_trigger_ids - defined)
        if unknown:
            findings.append(
                Finding(
                    ERROR,
                    UNKNOWN_TRIGGER_ID,
                    [unit.entity_id],
                    {
                        "automation": unit.name,
                        "ids": ", ".join(unknown),
                        "defined": ", ".join(sorted(defined)) or "–",
                    },
                )
            )
        if unit.trigger_guarded_choose and not unit.has_default_path:
            idle = sorted(defined - unit.referenced_trigger_ids)
            no_id = sum(1 for t in unit.triggers if t.trigger_id is None)
            if idle or no_id:
                labels = idle + ([f"{no_id} × ohne ID / without ID"] if no_id else [])
                findings.append(
                    Finding(
                        NOTE,
                        IDLE_TRIGGER,
                        [unit.entity_id],
                        {"automation": unit.name, "ids": ", ".join(labels)},
                    )
                )
    return findings


def busy_entities(units: list[Unit], minimum: int = 4) -> list[tuple[str, list[str]]]:
    """Entitäten, die von vielen Automationen mit unterschiedlichen Werten gesetzt werden."""
    writers: dict[str, set[str]] = defaultdict(set)
    values: dict[str, set[str]] = defaultdict(set)
    names = {u.entity_id: u.name for u in units}
    for unit in units:
        if unit.kind != "automation":
            continue
        for write in unit.writes:
            if write.entity == "all":
                continue
            writers[write.entity].add(unit.entity_id)
            values[write.entity].add(f"{write.feature}={write.value}")
    return sorted(
        (
            (entity, sorted(names[u] for u in owners))
            for entity, owners in writers.items()
            if len(owners) >= minimum and len(values[entity]) > 1
        ),
        key=lambda item: (-len(item[1]), item[0]),
    )


def analyse(units: list[Unit], domains: dict[str, frozenset[str]] | None = None) -> list[Finding]:
    domains = domains or {}
    edges = build_edges(units, domains)
    findings = (
        find_trigger_id_issues(units) + find_loops(units, edges) + find_conflicts(units, domains)
    )
    order = {ERROR: 0, WARNING: 1, NOTE: 2}
    return sorted(findings, key=lambda f: (order[f.severity], f.kind, f.units))
