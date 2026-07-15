"""Diagnostics support for BTicino Companion."""

from __future__ import annotations

from dataclasses import asdict
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
    return async_redact_data({
        "entry": async_redact_data(dict(entry.data), {CONF_ACCESS_TOKEN}),
        "state": asdict(state) if state is not None else None,
        "transport": asdict(runtime.coordinator.runtime),
        "last_event": runtime.coordinator.last_event,
        "last_trace": asdict(runtime.coordinator.last_trace) if runtime.coordinator.last_trace else None,
    }, {CONF_ACCESS_TOKEN, "ip", "ip_address", "mac", "mac_address"})
