"""Konstanten für Automationscheck."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "automationscheck"

CONF_SHOW_NOTES: Final = "show_notes"
CONF_INCLUDE_DISABLED: Final = "include_disabled"

SERVICE_RUN: Final = "run"
SIGNAL_UPDATED: Final = f"{DOMAIN}_updated"

EVENT_AUTOMATION_RELOADED: Final = "automation_reloaded"
DEBOUNCE_SECONDS: Final = 10
PERIODIC_CHECK: Final = timedelta(hours=6)
