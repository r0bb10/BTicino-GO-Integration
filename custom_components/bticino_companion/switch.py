"""Switch platform for BTicino Companion."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
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
    entities: list[SwitchEntity] = []
    if _supports_mute(coordinator):
        entities.append(CompanionMuteSwitch(entry, coordinator, client))
    if _supports_voicemail(coordinator):
        entities.append(CompanionVoicemailSwitch(entry, coordinator, client))
    async_add_entities(entities)


def _supports_mute(coordinator: CompanionCoordinator) -> bool:
    data = coordinator.data if isinstance(coordinator.data, dict) else {}
    capabilities = data.get("capabilities", {}) if isinstance(data, dict) else {}
    cap_list = capabilities.get("capabilities", []) if isinstance(capabilities, dict) else []
    if isinstance(cap_list, list) and cap_list:
        return "control_audio_v2" in set(str(cap).strip() for cap in cap_list if isinstance(cap, str))
    return True


def _supports_voicemail(coordinator: CompanionCoordinator) -> bool:
    data = coordinator.data if isinstance(coordinator.data, dict) else {}
    state = data.get("state", {}) if isinstance(data, dict) else {}
    device = state.get("device", {}) if isinstance(state, dict) else {}
    model = str(device.get("model", "")).strip().upper()
    if model == "C100X":
        return False
    capabilities = data.get("capabilities", {}) if isinstance(data, dict) else {}
    cap_list = capabilities.get("capabilities", []) if isinstance(capabilities, dict) else []
    if isinstance(cap_list, list) and cap_list:
        return "control_voicemail_v2" in set(str(cap).strip() for cap in cap_list if isinstance(cap, str))
    return True


class CompanionMuteSwitch(CoordinatorEntity[CompanionCoordinator], SwitchEntity):
    """Mute switch backed by companion audio controls."""

    _attr_has_entity_name = True
    _attr_name = "Mute"
    _attr_icon = "mdi:microphone-off"

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        client,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_mute"

    @property
    def device_info(self):
        return build_device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        return self.coordinator.entities_available

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        state = data.get("state", {}) if isinstance(data, dict) else {}
        audio = state.get("audio", {}) if isinstance(state, dict) else {}
        return bool((audio if isinstance(audio, dict) else {}).get("muted", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        del kwargs
        await self.coordinator.async_run_command(
            label="Mute",
            command_coro_factory=self._client.async_audio_mute,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        del kwargs
        await self.coordinator.async_run_command(
            label="Unmute",
            command_coro_factory=self._client.async_audio_unmute,
        )


class CompanionVoicemailSwitch(CoordinatorEntity[CompanionCoordinator], SwitchEntity):
    """Voicemail switch backed by companion voicemail controls."""

    _attr_has_entity_name = True
    _attr_name = "Voicemail"
    _attr_icon = "mdi:voicemail"

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        client,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_voicemail"

    @property
    def device_info(self):
        return build_device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        return self.coordinator.entities_available and _supports_voicemail(self.coordinator)

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        state = data.get("state", {}) if isinstance(data, dict) else {}
        voicemail = state.get("voicemail", {}) if isinstance(state, dict) else {}
        return bool((voicemail if isinstance(voicemail, dict) else {}).get("enabled", False))

    async def async_turn_on(self, **kwargs: Any) -> None:
        del kwargs
        await self.coordinator.async_run_command(
            label="Voicemail enable",
            command_coro_factory=self._client.async_voicemail_enable,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        del kwargs
        await self.coordinator.async_run_command(
            label="Voicemail disable",
            command_coro_factory=self._client.async_voicemail_disable,
        )
