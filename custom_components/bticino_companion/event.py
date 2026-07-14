"""Event entity for multiplexed OpenWebNet traces."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .coordinator import CompanionCoordinator
from .device_info import device_info
from .models import TraceFrame


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the disabled-by-default trace event entity."""
    del hass
    runtime: IntegrationRuntime = entry.runtime_data
    async_add_entities([CompanionTraceEvent(entry, runtime.coordinator)])


class CompanionTraceEvent(CoordinatorEntity[CompanionCoordinator], EventEntity):
    """Publish frames already multiplexed through the coordinator WebSocket."""

    _attr_has_entity_name = True
    _attr_name = "OpenWebNet Trace"
    _attr_icon = "mdi:timeline-text"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_event_types = ["rx", "tx", "unknown"]

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._remove_listener = None
        self._attr_unique_id = f"{entry.entry_id}_openwebnet_trace"

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _handle_trace(trace: TraceFrame) -> None:
            event_type = trace.direction if trace.direction in {"rx", "tx"} else "unknown"
            self._trigger_event(event_type, {"frame": trace.frame})
            self.async_write_ha_state()

        self._remove_listener = self.coordinator.async_add_trace_listener(_handle_trace)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()
        await super().async_will_remove_from_hass()
