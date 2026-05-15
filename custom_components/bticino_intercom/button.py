"""Button platform for BTicino Companion entrypoint actions."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .api import CompanionApiClient
from .coordinator import CompanionCoordinator
from .device_info import build_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: IntegrationRuntime = entry.runtime_data
    coordinator = runtime.coordinator
    client = runtime.client

    known_entrypoint_ids: set[str] = set()

    def _sync_unlock_buttons() -> None:
        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        entrypoints_container = data.get("entrypoints", {}) if isinstance(data, dict) else {}
        rows = entrypoints_container.get("entrypoints", []) if isinstance(entrypoints_container, dict) else []
        if not isinstance(rows, list):
            return

        new_entities: list[CompanionEntrypointUnlockButton] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("has_unlock") is False:
                continue

            entrypoint_id = str(row.get("id", "")).strip()
            if not entrypoint_id or entrypoint_id in known_entrypoint_ids:
                continue

            label = str(row.get("label") or entrypoint_id).strip() or entrypoint_id
            known_entrypoint_ids.add(entrypoint_id)
            new_entities.append(
                CompanionEntrypointUnlockButton(
                    entry=entry,
                    coordinator=coordinator,
                    client=client,
                    entrypoint_id=entrypoint_id,
                    entrypoint_label=label,
                )
            )

        if new_entities:
            async_add_entities(new_entities)

    _sync_unlock_buttons()
    entry.async_on_unload(coordinator.async_add_listener(_sync_unlock_buttons))


class CompanionEntrypointUnlockButton(CoordinatorEntity[CompanionCoordinator], ButtonEntity):
    """Button to unlock a specific entrypoint."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:door-open"

    def __init__(
        self,
        *,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        client: CompanionApiClient,
        entrypoint_id: str,
        entrypoint_label: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._entrypoint_id = entrypoint_id
        self._attr_name = f"Unlock {entrypoint_label}"
        self._attr_unique_id = f"{entry.entry_id}_entrypoint_unlock_{entrypoint_id}"

    @property
    def device_info(self):
        return build_device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        return self.coordinator.entities_available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"entrypoint_id": self._entrypoint_id}

    async def async_press(self) -> None:
        await self.coordinator.async_run_command(
            label=f"Entrypoint unlock ({self._entrypoint_id})",
            command_coro_factory=lambda: self._client.async_entrypoint_unlock(self._entrypoint_id),
        )
