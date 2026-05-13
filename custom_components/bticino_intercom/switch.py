"""Switch platform for BTicino Companion."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .const import DOMAIN, NAME
from .coordinator import CompanionCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: IntegrationRuntime = entry.runtime_data
    coordinator = runtime.coordinator
    client = runtime.client
    async_add_entities([CompanionMuteSwitch(entry, coordinator, client)])


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
    def device_info(self) -> DeviceInfo:
        state = self.coordinator.data.get("state", {}) if isinstance(self.coordinator.data, dict) else {}
        device = state.get("device", {}) if isinstance(state, dict) else {}
        model = str(device.get("model", "")).strip() or "Companion"
        firmware = str(device.get("firmware", "")).strip() or None
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.unique_id or self._entry.entry_id)},
            name=NAME,
            manufacturer="BTicino",
            model=model,
            sw_version=firmware,
        )

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
