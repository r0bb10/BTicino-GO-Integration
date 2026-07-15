"""Switch entities for Companion controls."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import IntegrationRuntime
from .coordinator import CompanionCoordinator
from .device_info import device_info
from .entity import CompanionAvailabilityMixin


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up audio mute and dynamically supported voicemail controls."""
    del hass
    runtime: IntegrationRuntime = entry.runtime_data
    async_add_entities([CompanionMuteSwitch(entry, runtime.coordinator)])
    added_voicemail = False

    def _add_voicemail() -> None:
        nonlocal added_voicemail
        if not added_voicemail and runtime.coordinator.data and runtime.coordinator.data.voicemail_enabled is not None:
            added_voicemail = True
            async_add_entities([CompanionVoicemailSwitch(entry, runtime.coordinator)])

    _add_voicemail()
    entry.async_on_unload(runtime.coordinator.async_add_listener(_add_voicemail))


class _CompanionSwitch(CompanionAvailabilityMixin, CoordinatorEntity[CompanionCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)


class CompanionMuteSwitch(_CompanionSwitch):
    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator) -> None:
        super().__init__(entry, coordinator, "mute", "Mute", "mdi:microphone-off")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.muted)

    async def async_turn_on(self, **kwargs: Any) -> None:
        del kwargs
        await self.coordinator.async_command("audio.mute")

    async def async_turn_off(self, **kwargs: Any) -> None:
        del kwargs
        await self.coordinator.async_command("audio.unmute")


class CompanionVoicemailSwitch(_CompanionSwitch):
    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator) -> None:
        super().__init__(entry, coordinator, "voicemail", "Voicemail", "mdi:voicemail")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data and self.coordinator.data.voicemail_enabled)

    async def async_turn_on(self, **kwargs: Any) -> None:
        del kwargs
        await self.coordinator.async_command("voicemail.enable")

    async def async_turn_off(self, **kwargs: Any) -> None:
        del kwargs
        await self.coordinator.async_command("voicemail.disable")
