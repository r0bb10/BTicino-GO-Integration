"""Button entities for Companion commands."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .coordinator import CompanionCoordinator
from .device_info import device_info
from .models import Entrypoint


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add capability-gated action entities as entrypoints arrive."""
    runtime: IntegrationRuntime = entry.runtime_data
    known: set[str] = set()

    def _add_entities() -> None:
        state = runtime.coordinator.data
        if state is None:
            return
        entities: list[ButtonEntity] = []
        for entrypoint in state.entrypoints:
            unique_id = f"{entry.entry_id}_unlock_{entrypoint.id}"
            if entrypoint.capabilities.unlock and unique_id not in known:
                known.add(unique_id)
                entities.append(CompanionUnlockButton(entry, runtime.coordinator, entrypoint))
        if state.reboot_enabled and f"{entry.entry_id}_reboot" not in known:
            known.add(f"{entry.entry_id}_reboot")
            entities.append(CompanionCommandButton(entry, runtime.coordinator, "reboot", "Reboot", "mdi:restart-alert", "system.reboot"))
        for service in state.services:
            unique_id = f"{entry.entry_id}_restart_{service.name}"
            if service.enabled and service.exposed and unique_id not in known:
                known.add(unique_id)
                entities.append(CompanionCommandButton(entry, runtime.coordinator, f"restart_{service.name}", f"Restart {service.name.title()}", "mdi:restart", "system.service.restart", {"service": service.name}))
        if entities:
            async_add_entities(entities)

    _add_entities()
    entry.async_on_unload(runtime.coordinator.async_add_listener(_add_entities))


class CompanionCommandButton(CoordinatorEntity[CompanionCoordinator], ButtonEntity):
    """Run a fixed v3 command using only this entity's context."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, key: str, name: str, icon: str, action: str, payload: dict[str, str] | None = None) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._action = action
        self._payload = payload
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        return self.coordinator.runtime.connected

    async def async_press(self) -> None:
        await self.coordinator.async_command(self._action, self._payload)


class CompanionUnlockButton(CompanionCommandButton):
    """Unlock one configured entrypoint."""

    _attr_entity_category = None

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, entrypoint: Entrypoint) -> None:
        super().__init__(entry, coordinator, f"unlock_{entrypoint.id}", f"Unlock {entrypoint.label or entrypoint.id}", "mdi:door-open", "entrypoint.unlock", {"entrypoint_id": entrypoint.id})
