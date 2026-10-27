"""Statische Analyse von Home-Assistant-Automationen (ohne HA-Abhängigkeiten)."""

from .analysis import (
    CONFLICT,
    ERROR,
    IDLE_TRIGGER,
    LOOP,
    NOTE,
    SELF_TRIGGER,
    UNKNOWN_TRIGGER_ID,
    WARNING,
    Finding,
    analyse,
    busy_entities,
)
from .model import Unit, build_unit, inline_scripts

__all__ = [
    "CONFLICT",
    "ERROR",
    "IDLE_TRIGGER",
    "LOOP",
    "NOTE",
    "SELF_TRIGGER",
    "UNKNOWN_TRIGGER_ID",
    "WARNING",
    "Finding",
    "Unit",
    "analyse",
    "build_unit",
    "busy_entities",
    "inline_scripts",
]
