"""Binary sensor platform for BTicino Companion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
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
class CompanionBinarySensorDescription:
    key: str
    name: str
    icon: str
    device_class: BinarySensorDeviceClass | None
    entity_category: EntityCategory | None
    value_fn: Callable[[dict[str, Any], CompanionCoordinator], bool]
    strict_availability: bool = True


SENSORS: tuple[CompanionBinarySensorDescription, ...] = (
    CompanionBinarySensorDescription(
        key="connected",
        name="Companion Connected",
        icon="mdi:lan-connect",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _data, coordinator: coordinator.last_update_success and not coordinator.sse_stale,
        strict_availability=False,
    ),
    CompanionBinarySensorDescription(
        key="ringing",
        name="Ringing",
        icon="mdi:bell-ring",
        device_class=None,
        entity_category=None,
        value_fn=lambda data, _coordinator: bool(data.get("state", {}).get("ringing", False)),
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
        CompanionBinarySensorEntity(entry, coordinator, description)
        for description in SENSORS
    ]
    async_add_entities(entities)


class CompanionBinarySensorEntity(CoordinatorEntity[CompanionCoordinator], BinarySensorEntity):
    """Coordinator-backed BTicino binary sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        description: CompanionBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_icon = description.icon
        self._attr_device_class = description.device_class
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
    def is_on(self) -> bool:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        return bool(self.entity_description.value_fn(data, self.coordinator))
