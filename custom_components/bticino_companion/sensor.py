"""Sensors for pushed BTicino Companion state."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .device_info import device_info
from .entity import CompanionAvailabilityMixin
from .coordinator import CompanionCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the state sensors."""
    del hass
    runtime: IntegrationRuntime = entry.runtime_data
    async_add_entities([
        CompanionStateSensor(entry, runtime.coordinator, "active_entrypoint", "Active Entrypoint", "mdi:gate"),
        CompanionDiagnosticSensor(entry, runtime.coordinator, "ip_address", "IP Address", "mdi:ip-network"),
        CompanionDiagnosticSensor(entry, runtime.coordinator, "mac_address", "MAC Address", "mdi:network"),
        CompanionDiagnosticSensor(entry, runtime.coordinator, "wifi_strength", "WiFi Strength", "mdi:wifi"),
    ])


class CompanionStateSensor(CompanionAvailabilityMixin, CoordinatorEntity[CompanionCoordinator], SensorEntity):
    """Expose one scalar from the pushed Companion state."""

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)

    @property
    def native_value(self) -> str:
        state = self.coordinator.data
        if state is None:
            return "unknown"
        if self._key == "active_entrypoint":
            return state.active_entrypoint_id or "none"
        return state.call_state


class CompanionDiagnosticSensor(CompanionAvailabilityMixin, CoordinatorEntity[CompanionCoordinator], SensorEntity):
    """Expose one value from the cached pushed diagnostics."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        if key == "wifi_strength":
            self._attr_native_unit_of_measurement = "%"

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)

    @property
    def native_value(self) -> str | int | None:
        state = self.coordinator.data
        return getattr(state.diagnostics, self._key) if state is not None else None
