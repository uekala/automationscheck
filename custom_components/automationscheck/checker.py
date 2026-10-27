"""Liest Automationen und Skripte aus Home Assistant, prüft sie und meldet Befunde."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .const import (
    CONF_INCLUDE_DISABLED,
    CONF_SHOW_NOTES,
    DEBOUNCE_SECONDS,
    DOMAIN,
    EVENT_AUTOMATION_RELOADED,
    PERIODIC_CHECK,
    SIGNAL_UPDATED,
)
from .engine import ERROR, NOTE, WARNING, Finding, Unit, analyse, build_unit, inline_scripts

_LOGGER = logging.getLogger(__name__)

_RELOAD_DOMAINS = {"automation", "script", "input_select"}


@dataclass
class CheckResult:
    """Ergebnis eines Prüflaufs."""

    findings: list[Finding]
    checked: int
    skipped: list[str]
    finished: datetime
    names: dict[str, str] = field(default_factory=dict)

    def shown(self, show_notes: bool) -> list[Finding]:
        levels = {ERROR, WARNING} | ({NOTE} if show_notes else set())
        return [f for f in self.findings if f.severity in levels]

    def as_dicts(self, show_notes: bool) -> list[dict[str, Any]]:
        return [
            {
                "severity": f.severity,
                "type": f.kind,
                "automations": [self.names.get(u, u) for u in f.units],
                "entity_ids": f.units,
                **({"entity": f.entity} if f.entity else {}),
                **({"chain": f.lines} if f.lines else {}),
                **{k: v for k, v in f.placeholders.items() if k not in ("automations",)},
            }
            for f in self.shown(show_notes)
        ]


def _name(hass: HomeAssistant, entity: Any) -> str:
    state = hass.states.get(entity.entity_id)
    if state and isinstance(state.attributes.get("friendly_name"), str):
        return state.attributes["friendly_name"]
    name = getattr(entity, "name", None)
    return name if isinstance(name, str) else entity.entity_id


async def _async_substitute_blueprint(
    hass: HomeAssistant, kind: str, raw: dict[str, Any]
) -> dict[str, Any] | None:
    """Setzt Blueprint-Eingaben ein, damit auch Blueprint-Automationen geprüft werden."""
    try:
        if kind == "automation":
            from homeassistant.components.automation.helpers import async_get_blueprints
        else:
            from homeassistant.components.script.helpers import async_get_blueprints
        inputs = await async_get_blueprints(hass).async_inputs_from_config(raw)
        return dict(inputs.async_substitute())
    except Exception as err:
        _LOGGER.debug("Blueprint von %s nicht auflösbar: %s", raw.get("alias"), err)
        return None


class AutomationsCheck:
    """Führt die Prüfung aus und hält Reparaturen und Sensor aktuell."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.result: CheckResult | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=DEBOUNCE_SECONDS,
            immediate=False,
            function=self.async_run,
        )

    @property
    def show_notes(self) -> bool:
        return bool(self.entry.options.get(CONF_SHOW_NOTES, False))

    @property
    def include_disabled(self) -> bool:
        return bool(self.entry.options.get(CONF_INCLUDE_DISABLED, False))

    @callback
    def async_start(self) -> None:
        """Erste Prüfung nach dem Start, danach bei Reloads und regelmäßig."""

        @callback
        def _started(_hass: HomeAssistant) -> None:
            self.entry.async_create_background_task(
                self.hass, self.async_run(), f"{DOMAIN}_initial_check"
            )

        self._unsubs.append(async_at_started(self.hass, _started))
        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_AUTOMATION_RELOADED, self._async_schedule)
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                EVENT_CALL_SERVICE, self._async_schedule, event_filter=self._is_reload
            )
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._async_periodic, PERIODIC_CHECK)
        )

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()
        self._debouncer.async_shutdown()

    @staticmethod
    @callback
    def _is_reload(event_data: Any) -> bool:
        return event_data.get("domain") in _RELOAD_DOMAINS and event_data.get("service") == "reload"

    async def _async_schedule(self, _event: Event | None = None) -> None:
        await self._debouncer.async_call()

    async def _async_periodic(self, _now: datetime) -> None:
        await self.async_run()

    async def _async_collect(self) -> tuple[list[Unit], dict[str, frozenset[str]], list[str]]:
        units: dict[str, Unit] = {}
        skipped: list[str] = []
        for kind in ("automation", "script"):
            component = self.hass.data.get(kind)
            entities = list(getattr(component, "entities", []) or [])
            for entity in entities:
                name = _name(self.hass, entity)
                raw = getattr(entity, "raw_config", None)
                if not isinstance(raw, dict):
                    skipped.append(name)
                    continue
                if kind == "automation" and not self.include_disabled and not entity.is_on:
                    continue
                config: dict[str, Any] | None = copy.deepcopy(raw)
                if "use_blueprint" in raw:
                    config = await _async_substitute_blueprint(self.hass, kind, config)
                    if config is None:
                        skipped.append(name)
                        continue
                units[entity.entity_id] = build_unit(entity.entity_id, name, kind, config)
        inline_scripts(units)

        domains: dict[str, frozenset[str]] = {}
        for state in self.hass.states.async_all(("input_select", "select")):
            options = state.attributes.get("options")
            if isinstance(options, list) and options:
                domains[state.entity_id] = frozenset(str(o) for o in options)
        return list(units.values()), domains, skipped

    async def async_run(self) -> CheckResult:
        units, domains, skipped = await self._async_collect()
        findings = await self.hass.async_add_executor_job(analyse, units, domains)
        result = CheckResult(
            findings=findings,
            checked=len(units),
            skipped=sorted(skipped),
            finished=dt_util.utcnow(),
            names={u.entity_id: u.name for u in units},
        )
        self.result = result
        self._sync_issues(result)
        async_dispatcher_send(self.hass, f"{SIGNAL_UPDATED}_{self.entry.entry_id}")
        _LOGGER.debug("Prüfung fertig: %d Einheiten, %d Befunde", len(units), len(findings))
        return result

    @callback
    def _sync_issues(self, result: CheckResult) -> None:
        wanted = {f.key: f for f in result.shown(self.show_notes)}
        registry = ir.async_get(self.hass)
        for domain, issue_id in list(registry.issues):
            if domain == DOMAIN and issue_id not in wanted:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        for issue_id, finding in wanted.items():
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                is_persistent=False,
                severity=(
                    ir.IssueSeverity.ERROR
                    if finding.severity == ERROR
                    else ir.IssueSeverity.WARNING
                ),
                translation_key=finding.kind,
                translation_placeholders=placeholders(finding),
            )


def placeholders(finding: Finding) -> dict[str, str]:
    """Alle Platzhalter, die die Übersetzungen der Reparaturen erwarten."""
    values = {
        "automation": "",
        "automations": "",
        "automation_a": "",
        "automation_b": "",
        "entity": "",
        "value_a": "",
        "value_b": "",
        "trigger": "",
        "situation": "",
        "ids": "",
        "defined": "",
        "lines": "\n".join(f"- {line}" for line in finding.lines) or "–",
    }
    values.update(finding.placeholders)
    return values


@callback
def async_remove_issues(hass: HomeAssistant) -> None:
    registry = ir.async_get(hass)
    for domain, issue_id in list(registry.issues):
        if domain == DOMAIN:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
