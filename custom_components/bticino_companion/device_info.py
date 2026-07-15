"""Device registry metadata for BTicino Companion entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, NAME
from .models import CompanionState


def device_info(entry: ConfigEntry, state: CompanionState | None) -> DeviceInfo:
    """Build stable device metadata from the config entry and pushed state."""
    details: dict[str, str] = {}
    if state and state.diagnostics.firmware:
        details["sw_version"] = state.diagnostics.firmware
    if state and state.diagnostics.hardware:
        details["hw_version"] = state.diagnostics.hardware
    return DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id)},
        name=NAME,
        manufacturer="BTicino",
        model=state.model if state and state.model else "Companion",
        **details,
    )
