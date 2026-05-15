"""Sensor platform for BTicino Companion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .coordinator import CompanionCoordinator
from .device_info import build_device_info


@dataclass(frozen=True, kw_only=True)
class CompanionSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any], CompanionCoordinator], Any]
    strict_availability: bool = True


SENSORS: tuple[CompanionSensorDescription, ...] = (
    CompanionSensorDescription(
        key="call_state",
        name="Call State",
        icon="mdi:phone",
        value_fn=lambda data, _: data.get("state", {}).get("call_state", "unknown"),
    ),
    CompanionSensorDescription(
        key="active_entrypoint",
        name="Active Entrypoint",
        icon="mdi:map-marker-path",
        value_fn=lambda data, _: _active_entrypoint_value(data),
    ),
    CompanionSensorDescription(
        key="network_ip",
        name="IP Address",
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data, _: _network_value(data, "ip"),
    ),
    CompanionSensorDescription(
        key="network_mac",
        name="Mac Address",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data, _: _network_value(data, "mac"),
        strict_availability=False,
    ),
    CompanionSensorDescription(
        key="network_wifi_signal",
        name="WiFi Strength",
        icon="mdi:wifi-strength-3",
        native_unit_of_measurement="%",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data, _: _wifi_signal_percent(data),
    ),
)


def _active_entrypoint_value(data: dict[str, Any]) -> str:
    state = data.get("state", {}) if isinstance(data, dict) else {}
    value = state.get("active_entrypoint") if isinstance(state, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "none"


def _network_value(data: dict[str, Any], key: str) -> str | None:
    state = data.get("state", {}) if isinstance(data, dict) else {}
    diagnostics = state.get("diagnostics", {}) if isinstance(state, dict) else {}
    network = diagnostics.get("network", {}) if isinstance(diagnostics, dict) else {}
    value = str(network.get(key, "")).strip() if isinstance(network, dict) else ""
    return value or None


def _wifi_strength(data: dict[str, Any]) -> int | None:
    state = data.get("state", {}) if isinstance(data, dict) else {}
    diagnostics = state.get("diagnostics", {}) if isinstance(state, dict) else {}
    network = diagnostics.get("network", {}) if isinstance(diagnostics, dict) else {}
    if not isinstance(network, dict):
        return None
    value = network.get("wifi_strength")
    if isinstance(value, (int, float)):
        return max(0, min(100, int(value)))
    value = network.get("wifi_rssi")
    if isinstance(value, (int, float)):
        # Backward compatibility when companion still emits only wifi_rssi.
        return max(0, min(100, int(value)))
    return None


def _wifi_signal_percent(data: dict[str, Any]) -> int | None:
    return _wifi_strength(data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: IntegrationRuntime = entry.runtime_data
    coordinator = runtime.coordinator

    entities = [
        CompanionSensorEntity(entry, coordinator, description)
        for description in SENSORS
    ]
    async_add_entities(entities)


class CompanionSensorEntity(CoordinatorEntity[CompanionCoordinator], SensorEntity):
    """Coordinator-backed BTicino sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        description: CompanionSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_icon = description.icon
        self._attr_entity_category = description.entity_category

    @property
    def device_info(self):
        return build_device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        if not self.entity_description.strict_availability:
            return True
        return self.coordinator.entities_available

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        return self.entity_description.value_fn(data, self.coordinator)
