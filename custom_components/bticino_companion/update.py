"""Companion firmware update entity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .coordinator import CompanionCoordinator
from .device_info import device_info
from .entity import CompanionAvailabilityMixin


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add the update entity only when the server explicitly exposes it."""
    del hass
    runtime: IntegrationRuntime = entry.runtime_data
    added = False

    def _add_update() -> None:
        nonlocal added
        state = runtime.coordinator.data
        if not added and state and state.update.enabled and state.update.exposed:
            added = True
            async_add_entities([CompanionUpdate(entry, runtime.coordinator)])

    _add_update()
    entry.async_on_unload(runtime.coordinator.async_add_listener(_add_update))


class CompanionUpdate(CompanionAvailabilityMixin, CoordinatorEntity[CompanionCoordinator], UpdateEntity):
    """Install the release selected by the Companion v3 updater."""

    _attr_has_entity_name = True
    _attr_name = "Companion Firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator) -> None:
        super().__init__(coordinator)
        self._entry = entry
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

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Request the configured v3 updater; version selection is server-owned."""
        del version, backup, kwargs
        await self.coordinator.async_command("update.install")
