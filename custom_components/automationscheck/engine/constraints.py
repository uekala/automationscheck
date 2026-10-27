"""Zustandsbedingungen als Mengen – um zu prüfen, ob zwei Pfade zugleich möglich sind.

Jede Entität bekommt eine erlaubte Menge (oder "beliebig") und eine Menge
ausgeschlossener Zustände. Mit dem Wertebereich einer Entität (z. B. den
Optionen eines input_select) lässt sich dann entscheiden, ob es überhaupt
einen Zustand gibt, der alle Bedingungen gleichzeitig erfüllt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .model import Trigger, as_list, is_template

BINARY_DOMAINS = {
    "input_boolean",
    "switch",
    "light",
    "fan",
    "binary_sensor",
    "automation",
    "siren",
    "remote",
    "humidifier",
}
KNOWN_DOMAINS = {
    "alarm_control_panel": frozenset(
        {
            "disarmed",
            "armed_home",
            "armed_away",
            "armed_night",
            "armed_vacation",
            "armed_custom_bypass",
            "pending",
            "arming",
            "triggered",
        }
    ),
    "lock": frozenset({"locked", "unlocked", "locking", "unlocking", "jammed", "open", "opening"}),
}


@dataclass
class Constraint:
    allowed: frozenset[str] | None = None
    excluded: frozenset[str] = field(default_factory=frozenset)

    def merge(self, other: Constraint) -> Constraint:
        if self.allowed is None:
            allowed = other.allowed
        elif other.allowed is None:
            allowed = self.allowed
        else:
            allowed = self.allowed & other.allowed
        return Constraint(allowed, self.excluded | other.excluded)


Constraints = dict[str, Constraint]


def domain_of(entity: str, domains: dict[str, frozenset[str]]) -> frozenset[str] | None:
    if entity in domains:
        return domains[entity]
    kind = entity.split(".", 1)[0]
    if kind in BINARY_DOMAINS:
        return frozenset({"on", "off"})
    return KNOWN_DOMAINS.get(kind)


def _states(value: Any) -> frozenset[str] | None:
    states = [str(v) for v in as_list(value) if v is not None]
    if not states or any(is_template(s) for s in states):
        return None
    return frozenset(states)


def _add(result: Constraints, entity: str, constraint: Constraint) -> None:
    result[entity] = result[entity].merge(constraint) if entity in result else constraint


def from_conditions(conditions: list[dict]) -> Constraints:
    """UND-Verknüpfung einer Bedingungsliste; Unbekanntes wird ignoriert (= offen)."""
    result: Constraints = {}
    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        kind = cond.get("condition")
        if kind == "state" and not cond.get("attribute"):
            states = _states(cond.get("state"))
            if states is None:
                continue
            for entity in as_list(cond.get("entity_id")):
                if isinstance(entity, str):
                    _add(result, entity, Constraint(allowed=states))
        elif kind == "and":
            for entity, constraint in from_conditions(as_list(cond.get("conditions"))).items():
                _add(result, entity, constraint)
        elif kind == "not":
            # HA: "not" ist wahr, wenn KEINE der inneren Bedingungen zutrifft
            for inner in as_list(cond.get("conditions")):
                if not isinstance(inner, dict) or inner.get("condition") != "state":
                    continue
                if inner.get("attribute"):
                    continue
                states = _states(inner.get("state"))
                if states is None:
                    continue
                for entity in as_list(inner.get("entity_id")):
                    if isinstance(entity, str):
                        _add(result, entity, Constraint(excluded=states))
    return result


def from_trigger(trigger: Trigger) -> Constraints:
    """Was ein Auslöser über den Zustand seiner Entität im Moment des Feuerns verrät."""
    if trigger.kind != "state" or not trigger.entity or trigger.attribute:
        return {}
    constraint = Constraint()
    if trigger.to:
        constraint = constraint.merge(Constraint(allowed=frozenset(trigger.to)))
    excluded = set(trigger.not_to)
    if trigger.from_ and not trigger.to and len(trigger.from_) == 1:
        # Zustand hat sich geändert: der neue Zustand ist nicht der alte
        excluded |= set(trigger.from_)
    if excluded:
        constraint = constraint.merge(Constraint(excluded=frozenset(excluded)))
    if constraint.allowed is None and not constraint.excluded:
        return {}
    return {trigger.entity: constraint}


def merge(*parts: Constraints) -> Constraints:
    result: Constraints = {}
    for part in parts:
        for entity, constraint in part.items():
            _add(result, entity, constraint)
    return result


def without(constraints: Constraints, entity: str) -> Constraints:
    return {e: c for e, c in constraints.items() if e != entity}


def satisfiable(constraints: Constraints, domains: dict[str, frozenset[str]]) -> bool:
    for entity, constraint in constraints.items():
        allowed = constraint.allowed
        if allowed is None:
            allowed = domain_of(entity, domains)
            if allowed is None:
                continue  # Wertebereich unbekannt: Ausschlüsse allein widersprechen nie
        if not (allowed - constraint.excluded):
            return False
    return True
