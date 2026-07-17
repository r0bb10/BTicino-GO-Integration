"""Companion firmware update entity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback, async_get_current_platform
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .coordinator import CompanionCoordinator
from .device_info import device_info
from .entity import CompanionAvailabilityMixin


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Register firmware update lifecycle management."""
    del hass, async_add_entities
    runtime: IntegrationRuntime = entry.runtime_data
    await runtime.dynamic_entities.async_register_platform("update", async_get_current_platform())


class CompanionUpdate(CompanionAvailabilityMixin, CoordinatorEntity[CompanionCoordinator], UpdateEntity):
    """Install the release selected by the Companion v3 updater."""

    _attr_has_entity_name = True
    _attr_name = "Companion Firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, client) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.unique_id}_firmware_update"

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        state = self.coordinator.data
        return bool(super().available and state and state.update.enabled and state.update.exposed)

    @property
    def installed_version(self) -> str | None:
        return self.coordinator.data.update.installed_version if self.coordinator.data else None

    @property
    def latest_version(self) -> str | None:
        state = self.coordinator.data
        return state.update.latest_version if state else None

    @property
    def in_progress(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.update.in_progress)

    @property
    def extra_state_attributes(self) -> dict[str, str | bool | None]:
        update = self.coordinator.data.update if self.coordinator.data else None
        if update is None:
            return {}
        return {
            "stage": update.stage,
            "staged_version": update.staged_version,
            "restart_required": update.restart_required,
            "last_error": update.last_error,
        }

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Request the configured v3 updater; version selection is server-owned."""
        del version, backup, kwargs
        await self._client.async_install_update()
