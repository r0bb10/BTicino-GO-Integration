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
from .entity import CompanionAvailabilityMixin
from .models import CompanionState, Entrypoint


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add capability-gated action entities as entrypoints arrive."""
    del hass
    runtime: IntegrationRuntime = entry.runtime_data
    known: set[str] = set()

    def _add_entities() -> None:
        state = runtime.coordinator.data
        if state is None:
            return
        entities: list[ButtonEntity] = []
        entities.extend(_entrypoint_buttons(entry, runtime.coordinator, runtime.client, state, known))
        if state.reboot_enabled:
            unique_id = f"{entry.unique_id}_reboot"
            if unique_id not in known:
                known.add(unique_id)
                entities.append(CompanionRebootButton(entry, runtime.coordinator, runtime.client))
        if entities:
            async_add_entities(entities)

    _add_entities()
    entry.async_on_unload(runtime.coordinator.async_add_listener(_add_entities))


def _entrypoint_buttons(
    entry: ConfigEntry, coordinator: CompanionCoordinator, client, state: CompanionState, known: set[str]
) -> list[ButtonEntity]:
    entities: list[ButtonEntity] = []
    for entrypoint in state.entrypoints:
        base = f"{entry.unique_id}_{entrypoint.id}"
        if entrypoint.capabilities.unlock:
            unique_id = f"{base}_unlock"
            if unique_id not in known:
                known.add(unique_id)
                entities.append(
                    CompanionEntrypointButton(
                        entry,
                        coordinator,
                        client,
                        entrypoint,
                        key=f"unlock_{entrypoint.id}",
                        name=entrypoint.label or entrypoint.id,
                        icon="mdi:door-open",
                    )
                )
    return entities


class _CompanionButton(CompanionAvailabilityMixin, CoordinatorEntity[CompanionCoordinator], ButtonEntity):
    """Base for fixed v3 command buttons."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)


class CompanionEntrypointButton(_CompanionButton):
    """Run a fixed entrypoint-scoped v3 command."""

    _attr_entity_category = None

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        client,
        entrypoint: Entrypoint,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(entry, coordinator, key, name, icon)
        self._entrypoint = entrypoint
        self._client = client

    @property
    def available(self) -> bool:
        return super().available and self._entrypoint.availability.unlock

    async def async_press(self) -> None:
        await self._client.async_unlock_entrypoint(self._entrypoint.id)


class CompanionRebootButton(_CompanionButton):
    """Reboot the intercom through the typed REST control endpoint."""

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, client) -> None:
        super().__init__(entry, coordinator, "reboot", "Reboot", "mdi:restart")
        self._client = client

    async def async_press(self) -> None:
        await self._client.async_reboot()
