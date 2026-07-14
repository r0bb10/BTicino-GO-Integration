"""Diagnostics support for BTicino Companion."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import IntegrationRuntime
from .const import CONF_ACCESS_TOKEN


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return local runtime diagnostics without secrets or raw media data."""
    del hass
    runtime: IntegrationRuntime = entry.runtime_data
    state = runtime.coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), {CONF_ACCESS_TOKEN}),
        "state": state,
        "transport": runtime.coordinator.runtime,
        "last_event": runtime.coordinator.last_event,
        "last_trace": runtime.coordinator.last_trace,
    }
