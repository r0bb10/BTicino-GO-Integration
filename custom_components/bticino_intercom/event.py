"""Event platform for BTicino OpenWebNet traces."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IntegrationRuntime
from .const import DOMAIN, NAME
from .trace_relay import trace_signal

_EVENT_TYPES = ["rx", "tx", "info", "error", "unknown"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: IntegrationRuntime = entry.runtime_data
    async_add_entities([CompanionOpenWebNetTraceEvent(runtime, entry)])


class CompanionOpenWebNetTraceEvent(EventEntity):
    """Event entity carrying last OpenWebNet trace frame."""

    _attr_has_entity_name = True
    _attr_name = "OpenWebNet Trace"
    _attr_icon = "mdi:timeline-text"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_event_types = _EVENT_TYPES

    def __init__(
        self,
        runtime: IntegrationRuntime,
        entry: ConfigEntry,
    ) -> None:
        self.runtime = runtime
        self.coordinator = runtime.coordinator
        self._entry = entry
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_openwebnet_trace"
        self._remove_dispatcher = None

    @property
    def device_info(self) -> DeviceInfo:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        state = data.get("state", {}) if isinstance(data, dict) else {}
        device = state.get("device", {}) if isinstance(state, dict) else {}
        model = str(device.get("model", "")).strip() or "Companion"
        firmware = str(device.get("firmware", "")).strip() or None
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
            name=NAME,
            manufacturer="BTicino",
            model=model,
            sw_version=firmware,
        )

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _handle_trace(payload: dict[str, Any]) -> None:
            event_type = _trace_event_type(payload)
            attributes = {k: v for k, v in payload.items() if k != "entry_id"}
            self._trigger_event(event_type, attributes)
            self.async_write_ha_state()

        self._remove_dispatcher = async_dispatcher_connect(
            self.hass,
            trace_signal(self._entry_id),
            _handle_trace,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_dispatcher is not None:
            self._remove_dispatcher()
            self._remove_dispatcher = None
        await super().async_will_remove_from_hass()


def _trace_event_type(payload: dict[str, Any]) -> str:
    direction = str(payload.get("direction", "")).strip().lower()
    if direction in {"rx", "tx"}:
        return direction
    if payload.get("error"):
        return "error"
    if payload.get("phase"):
        return "info"
    return "unknown"
