"""Sensor platform for BTicino Companion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .const import DOMAIN, NAME
from .coordinator import CompanionCoordinator


@dataclass(frozen=True, slots=True)
class CompanionSensorDescription:
    key: str
    name: str
    icon: str
    entity_category: EntityCategory | None
    value_fn: Callable[[dict[str, Any], CompanionCoordinator], Any]
    strict_availability: bool = True


SENSORS: tuple[CompanionSensorDescription, ...] = (
    CompanionSensorDescription(
        key="call_state",
        name="Call State",
        icon="mdi:phone",
        entity_category=None,
        value_fn=lambda data, _: data.get("state", {}).get("call_state", "unknown"),
    ),
    CompanionSensorDescription(
        key="active_entrypoint",
        name="Active Entrypoint",
        icon="mdi:map-marker-path",
        entity_category=None,
        value_fn=lambda data, _: data.get("state", {}).get("active_entrypoint"),
    ),
    CompanionSensorDescription(
        key="entrypoints_count",
        name="Entrypoints Count",
        icon="mdi:door",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data, _: len(data.get("entrypoints", {}).get("entrypoints", [])),
        strict_availability=False,
    ),
    CompanionSensorDescription(
        key="sse_last_event_id",
        name="SSE Last Event ID",
        icon="mdi:counter",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _data, coordinator: coordinator.last_event_id,
        strict_availability=False,
    ),
)


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
    """Coordinator-backed BTicino v2 sensor."""

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
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
            name=NAME,
            manufacturer="BTicino",
            model="Companion",
        )

    @property
    def available(self) -> bool:
        if not self.entity_description.strict_availability:
            return True
        if not super().available:
            return False
        auth = self.coordinator.data.get("auth", {}) if isinstance(self.coordinator.data, dict) else {}
        if isinstance(auth, dict) and auth.get("needs_claim"):
            return False
        return not self.coordinator.sse_stale

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        return self.entity_description.value_fn(data, self.coordinator)
