"""Button entities for Companion commands."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
        entities.extend(_entrypoint_buttons(entry, runtime.coordinator, state, known))
        entities.extend(_call_buttons(entry, runtime.coordinator, state, known))
        entities.extend(_system_buttons(entry, runtime.coordinator, state, known))
        if entities:
            async_add_entities(entities)

    _add_entities()
    entry.async_on_unload(runtime.coordinator.async_add_listener(_add_entities))


def _entrypoint_buttons(
    entry: ConfigEntry, coordinator: CompanionCoordinator, state: CompanionState, known: set[str]
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
                        entrypoint,
                        key=f"unlock_{entrypoint.id}",
                        name=f"Unlock {entrypoint.label or entrypoint.id}",
                        icon="mdi:door-open",
                        action="entrypoint.unlock",
                    )
                )
        if entrypoint.capabilities.stream:
            for key, name, icon, action in (
                ("stream", "Stream", "mdi:video", "entrypoint.stream"),
                ("snapshot", "Snapshot", "mdi:camera", "entrypoint.snapshot"),
            ):
                unique_id = f"{base}_{key}"
                if unique_id not in known:
                    known.add(unique_id)
                    entities.append(
                        CompanionEntrypointButton(
                            entry,
                            coordinator,
                            entrypoint,
                            key=f"{key}_{entrypoint.id}",
                            name=f"{name} {entrypoint.label or entrypoint.id}",
                            icon=icon,
                            action=action,
                        )
                    )
    return entities


def _call_buttons(
    entry: ConfigEntry, coordinator: CompanionCoordinator, state: CompanionState, known: set[str]
) -> list[ButtonEntity]:
    entities: list[ButtonEntity] = []
    base = f"{entry.unique_id}_call"
    buttons = [
        ("answer", "Answer", "mdi:phone", "call.answer", _incoming_dialog(state)),
        ("decline", "Decline", "mdi:phone-hangup", "call.decline", _incoming_dialog(state)),
        ("hangup", "Hangup", "mdi:phone-off", "call.hangup", state.active_dialog_id),
    ]
    for key, name, icon, action, dialog_id in buttons:
        unique_id = f"{base}_{key}"
        if unique_id not in known:
            known.add(unique_id)
            entities.append(
                CompanionCallButton(
                    entry,
                    coordinator,
                    key=key,
                    name=name,
                    icon=icon,
                    action=action,
                    dialog_id=dialog_id,
                )
            )
    return entities


def _system_buttons(
    entry: ConfigEntry, coordinator: CompanionCoordinator, state: CompanionState, known: set[str]
) -> list[ButtonEntity]:
    entities: list[ButtonEntity] = []
    base = f"{entry.unique_id}_system"
    if state.reboot_enabled:
        unique_id = f"{base}_reboot"
        if unique_id not in known:
            known.add(unique_id)
            entities.append(
                CompanionCommandButton(
                    entry,
                    coordinator,
                    key="reboot",
                    name="Reboot",
                    icon="mdi:restart-alert",
                    action="system.reboot",
                )
            )
    for service in state.services:
        unique_id = f"{base}_restart_{service.name}"
        if service.enabled and service.exposed and unique_id not in known:
            known.add(unique_id)
            entities.append(
                CompanionCommandButton(
                    entry,
                    coordinator,
                    key=f"restart_{service.name}",
                    name=f"Restart {service.name.title()}",
                    icon="mdi:restart",
                    action="system.service_restart",
                    payload={"service": service.name},
                )
            )
    return entities


def _incoming_dialog(state: CompanionState) -> str | None:
    return state.incoming_dialog_id if state.call_state in {"incoming", "ringing"} else None


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


class CompanionCommandButton(_CompanionButton):
    """Run a fixed v3 command using only this entity's context."""

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        key: str,
        name: str,
        icon: str,
        action: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(entry, coordinator, key, name, icon)
        self._action = action
        self._payload = dict(payload or {})

    @property
    def available(self) -> bool:
        return super().available

    async def async_press(self) -> None:
        await self.coordinator.async_command(self._action, self._payload)


class CompanionEntrypointButton(_CompanionButton):
    """Run a fixed entrypoint-scoped v3 command."""

    _attr_entity_category = None

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        entrypoint: Entrypoint,
        key: str,
        name: str,
        icon: str,
        action: str,
    ) -> None:
        super().__init__(entry, coordinator, key, name, icon)
        self._entrypoint = entrypoint
        self._action = action

    @property
    def available(self) -> bool:
        return super().available and self._entrypoint.id in {ep.id for ep in self.coordinator.data.entrypoints}

    async def async_press(self) -> None:
        await self.coordinator.async_command(self._action, {"entrypoint_id": self._entrypoint.id})


class CompanionCallButton(_CompanionButton):
    """Run a call-scoped v3 command."""

    _attr_entity_category = None

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        key: str,
        name: str,
        icon: str,
        action: str,
        dialog_id: str | None,
    ) -> None:
        super().__init__(entry, coordinator, key, name, icon)
        self._action = action
        self._dialog_id = dialog_id

    @property
    def available(self) -> bool:
        if not super().available or not self._dialog_id:
            return False
        state = self.coordinator.data
        if state is None:
            return False
        if self._action == "call.hangup":
            return state.call_state == "active" and state.active_dialog_id == self._dialog_id
        return state.call_state in {"incoming", "ringing"} and state.incoming_dialog_id == self._dialog_id

    async def async_press(self) -> None:
        await self.coordinator.async_command(self._action, {"dialog_id": self._dialog_id})
