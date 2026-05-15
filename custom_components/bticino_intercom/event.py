"""Event platform for BTicino OpenWebNet traces."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .coordinator import CompanionCoordinator
from .device_info import build_device_info
from .trace_relay import trace_signal

_EVENT_TYPES = ["rx", "tx", "info", "error", "unknown"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: IntegrationRuntime = entry.runtime_data
    async_add_entities([CompanionOpenWebNetTraceEvent(runtime, entry)])


class CompanionOpenWebNetTraceEvent(CoordinatorEntity[CompanionCoordinator], EventEntity):
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
        super().__init__(runtime.coordinator)
        self.runtime = runtime
        self.coordinator = runtime.coordinator
        self._entry = entry
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_openwebnet_trace"
        self._remove_dispatcher = None

    @property
    def device_info(self):
        return build_device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        return self.coordinator.entities_available

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
