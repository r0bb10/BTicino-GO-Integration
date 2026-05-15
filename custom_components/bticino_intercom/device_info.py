"""Helpers to build integration-wide device metadata."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, NAME


def build_device_info(entry: ConfigEntry, data: dict[str, Any] | None) -> DeviceInfo:
    payload = data if isinstance(data, dict) else {}
    state = payload.get("state", {}) if isinstance(payload, dict) else {}
    device = state.get("device", {}) if isinstance(state, dict) else {}

    model = str(device.get("model", "")).strip() or "Companion"
    firmware = _optional_string(device.get("firmware"))
    hardware = _optional_string(device.get("hardware"))

    kwargs: dict[str, Any] = {}
    if firmware:
        kwargs["sw_version"] = firmware
    if hardware:
        kwargs["hw_version"] = hardware

    return DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        name=NAME,
        manufacturer="BTicino",
        model=model,
        **kwargs,
    )


def _optional_string(raw: Any) -> str | None:
    value = str(raw or "").strip()
    if not value or value.lower() == "unknown":
        return None
    return value
