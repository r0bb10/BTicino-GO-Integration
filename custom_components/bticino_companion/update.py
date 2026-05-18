"""Update platform for BTicino Companion."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import CompanionApiError
from .device_info import build_device_info

_IN_PROGRESS_STAGES = {"checking", "applying", "restarting", "rollback"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    client = runtime.client
    async_add_entities([CompanionFirmwareUpdateEntity(entry, coordinator, client)])


class CompanionFirmwareUpdateEntity(CoordinatorEntity, UpdateEntity):
    """Firmware update entity backed by companion update service."""

    _attr_has_entity_name = True
    _attr_name = "Companion"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, entry: ConfigEntry, coordinator, client) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_companion_firmware_update"

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        update = self._update_control
        return bool(update.get("enabled")) and bool(update.get("exposed"))

    @property
    def device_info(self):
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        return build_device_info(self._entry, data)

    @property
    def installed_version(self) -> str | None:
        value = self._status_data.get("current_version")
        if value is not None:
            return str(value)
        state = self.coordinator.data.get("state", {}) if isinstance(self.coordinator.data, dict) else {}
        if isinstance(state, dict):
            device = state.get("device", {})
            if isinstance(device, dict):
                fallback = device.get("firmware")
                if fallback is not None:
                    return str(fallback)
        return None

    @property
    def latest_version(self) -> str | None:
        available = self._status_data.get("available")
        if isinstance(available, dict):
            version = available.get("version")
            if version is not None:
                return str(version)
        return self.installed_version

    @property
    def in_progress(self) -> bool:
        stage = self._status_data.get("stage")
        if not isinstance(stage, str):
            return False
        return stage.strip().lower() in _IN_PROGRESS_STAGES

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self._status_data
        attrs: dict[str, Any] = {}
        for key in (
            "stage",
            "restart_required",
            "can_rollback",
            "last_checked_at",
            "last_applied_at",
            "last_rollback_at",
            "last_error",
        ):
            value = status.get(key)
            if value is not None:
                attrs[key] = value

        available = status.get("available")
        if isinstance(available, dict):
            for key in ("artifact_path", "sha256"):
                value = available.get(key)
                if value is not None:
                    attrs[key] = value

        attrs["allow_apply"] = bool(self._update_control.get("allow_apply", False))
        attrs["allow_rollback"] = bool(self._update_control.get("allow_rollback", False))
        return attrs

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        if version is not None:
            raise HomeAssistantError("Installing a specific version is not supported.")

        if not bool(self._update_control.get("allow_apply", False)):
            raise HomeAssistantError("Update apply is disabled in companion config.")

        try:
            await self.coordinator.async_run_command(
                label="Update check",
                command_coro_factory=lambda: self._client.async_update_check(force=True),
            )
            await self.coordinator.async_run_command(
                label="Update apply",
                command_coro_factory=self._client.async_update_apply,
            )
        except CompanionApiError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_request_refresh()

    @property
    def _update_control(self) -> dict[str, Any]:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        system_control = data.get("system_control", {})
        if not isinstance(system_control, dict):
            return {}
        update = system_control.get("update", {})
        return update if isinstance(update, dict) else {}

    @property
    def _status_data(self) -> dict[str, Any]:
        status = self._update_control.get("status", {})
        return status if isinstance(status, dict) else {}
