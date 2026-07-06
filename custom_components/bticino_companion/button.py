"""Button platform for BTicino Companion entrypoint actions."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .api import CompanionApiClient, CompanionApiError
from .coordinator import CompanionCoordinator
from .device_info import build_device_info
from .entity_registry import reconcile_platform_entities

_COMPANION_RESTART_VERIFY_ATTEMPTS = 30
_COMPANION_RESTART_VERIFY_DELAY_SECONDS = 1.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: IntegrationRuntime = entry.runtime_data
    coordinator = runtime.coordinator
    client = runtime.client

    known_entrypoint_ids: set[str] = set()
    known_service_names: set[str] = set()
    reboot_added = False

    def _sync_buttons() -> None:
        nonlocal reboot_added
        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        reconcile_platform_entities(
            hass,
            entry,
            platform_domain="button",
            desired_unique_ids=_desired_button_unique_ids(entry, data),
            managed_unique_ids={f"{entry.entry_id}_system_reboot"},
            managed_unique_id_prefixes={
                f"{entry.entry_id}_entrypoint_unlock_",
                f"{entry.entry_id}_system_service_restart_",
            },
        )

        entrypoints_container = data.get("entrypoints", {}) if isinstance(data, dict) else {}
        rows = entrypoints_container.get("entrypoints", []) if isinstance(entrypoints_container, dict) else []
        if not isinstance(rows, list):
            rows = []

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

        system_control = data.get("system_control", {}) if isinstance(data, dict) else {}
        if not isinstance(system_control, dict):
            return

        system_entities: list[ButtonEntity] = []
        if bool(system_control.get("reboot_enabled", False)):
            if not reboot_added:
                reboot_added = True
                system_entities.append(
                    CompanionSystemRebootButton(
                        entry=entry,
                        coordinator=coordinator,
                        client=client,
                    )
                )

        services = system_control.get("services", {})
        if isinstance(services, dict):
            for raw_name, raw_cfg in services.items():
                service_name = str(raw_name).strip().lower()
                if not service_name or service_name in known_service_names:
                    continue
                if not isinstance(raw_cfg, dict):
                    continue
                if not bool(raw_cfg.get("enabled", False)):
                    continue
                if not bool(raw_cfg.get("exposed", False)):
                    continue
                known_service_names.add(service_name)
                system_entities.append(
                    CompanionSystemServiceRestartButton(
                        entry=entry,
                        coordinator=coordinator,
                        client=client,
                        service_name=service_name,
                    )
                )

        if system_entities:
            async_add_entities(system_entities)

    _sync_buttons()
    entry.async_on_unload(coordinator.async_add_listener(_sync_buttons))


def _desired_button_unique_ids(entry: ConfigEntry, data: dict[str, Any]) -> set[str]:
    desired: set[str] = set()
    entrypoints_container = data.get("entrypoints", {}) if isinstance(data, dict) else {}
    rows = entrypoints_container.get("entrypoints", []) if isinstance(entrypoints_container, dict) else []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or row.get("has_unlock") is False:
                continue
            entrypoint_id = str(row.get("id", "")).strip()
            if entrypoint_id:
                desired.add(f"{entry.entry_id}_entrypoint_unlock_{entrypoint_id}")

    system_control = data.get("system_control", {}) if isinstance(data, dict) else {}
    if not isinstance(system_control, dict):
        return desired

    if bool(system_control.get("reboot_enabled", False)):
        desired.add(f"{entry.entry_id}_system_reboot")

    services = system_control.get("services", {})
    if isinstance(services, dict):
        for raw_name, raw_cfg in services.items():
            service_name = str(raw_name).strip().lower()
            if not service_name or not isinstance(raw_cfg, dict):
                continue
            if bool(raw_cfg.get("enabled", False)) and bool(raw_cfg.get("exposed", False)):
                desired.add(f"{entry.entry_id}_system_service_restart_{service_name}")
    return desired


def _entrypoint_supports_unlock(coordinator: CompanionCoordinator, entrypoint_id: str) -> bool:
    data = coordinator.data if isinstance(coordinator.data, dict) else {}
    entrypoints_container = data.get("entrypoints", {}) if isinstance(data, dict) else {}
    rows = entrypoints_container.get("entrypoints", []) if isinstance(entrypoints_container, dict) else []
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "")).strip() == entrypoint_id:
            return row.get("has_unlock") is not False
    return False


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
        self._attr_name = entrypoint_label
        self._attr_unique_id = f"{entry.entry_id}_entrypoint_unlock_{entrypoint_id}"

    @property
    def device_info(self):
        return build_device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        return self.coordinator.entities_available and _entrypoint_supports_unlock(
            self.coordinator,
            self._entrypoint_id,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"entrypoint_id": self._entrypoint_id}

    async def async_press(self) -> None:
        await self.coordinator.async_run_command(
            label=f"Entrypoint unlock ({self._entrypoint_id})",
            command_coro_factory=lambda: self._client.async_entrypoint_unlock(self._entrypoint_id),
        )


class CompanionSystemRebootButton(CoordinatorEntity[CompanionCoordinator], ButtonEntity):
    """Button to reboot the companion host."""

    _attr_has_entity_name = True
    _attr_name = "System Reboot"
    _attr_icon = "mdi:restart-alert"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        *,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        client: CompanionApiClient,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_system_reboot"

    @property
    def device_info(self):
        return build_device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        if not self.coordinator.entities_available:
            return False
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        system_control = data.get("system_control", {}) if isinstance(data, dict) else {}
        if not isinstance(system_control, dict):
            return False
        return bool(system_control.get("reboot_enabled", False))

    async def async_press(self) -> None:
        await self.coordinator.async_run_command(
            label="System reboot",
            command_coro_factory=self._client.async_system_reboot,
        )


class CompanionSystemServiceRestartButton(CoordinatorEntity[CompanionCoordinator], ButtonEntity):
    """Button to restart a specific system service."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:restart"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        *,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        client: CompanionApiClient,
        service_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._service_name = service_name.strip().lower()
        title = self._service_name.replace("_", " ").replace("-", " ").title()
        self._attr_name = f"Restart {title}"
        self._attr_unique_id = f"{entry.entry_id}_system_service_restart_{self._service_name}"

    @property
    def device_info(self):
        return build_device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        if not self.coordinator.entities_available:
            return False
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        system_control = data.get("system_control", {}) if isinstance(data, dict) else {}
        if not isinstance(system_control, dict):
            return False
        services = system_control.get("services", {})
        if not isinstance(services, dict):
            return False
        cfg = services.get(self._service_name, {})
        if not isinstance(cfg, dict):
            return False
        return bool(cfg.get("enabled", False)) and bool(cfg.get("exposed", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"service": self._service_name}

    async def async_press(self) -> None:
        if self._service_name == "companion":
            await self._async_press_companion_restart()
            return

        await self.coordinator.async_run_command(
            label=f"System service restart ({self._service_name})",
            command_coro_factory=lambda: self._client.async_system_service_restart(self._service_name),
        )

    async def _async_press_companion_restart(self) -> None:
        previous_boot_time = self._current_boot_time()
        try:
            await self.coordinator.async_run_command(
                label="System service restart (companion)",
                command_coro_factory=lambda: self._client.async_system_service_restart(self._service_name),
            )
            return
        except CompanionApiError as err:
            if not await self._async_verify_companion_restart(previous_boot_time):
                raise HomeAssistantError(str(err)) from err

        await self.coordinator.async_restart_event_stream()
        await self.coordinator.async_request_refresh()

    async def _async_verify_companion_restart(self, previous_boot_time: str) -> bool:
        saw_disconnect = False
        for _ in range(_COMPANION_RESTART_VERIFY_ATTEMPTS):
            await asyncio.sleep(_COMPANION_RESTART_VERIFY_DELAY_SECONDS)
            try:
                health = await self._client.async_get_health()
            except CompanionApiError:
                saw_disconnect = True
                continue

            if not isinstance(health, dict):
                continue
            current_boot_time = str(health.get("boot_time", "")).strip()
            if previous_boot_time and current_boot_time and current_boot_time != previous_boot_time:
                return True
            if not previous_boot_time and saw_disconnect:
                return True
        return False

    def _current_boot_time(self) -> str:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        health = data.get("health", {}) if isinstance(data, dict) else {}
        if not isinstance(health, dict):
            return ""
        return str(health.get("boot_time", "")).strip()
