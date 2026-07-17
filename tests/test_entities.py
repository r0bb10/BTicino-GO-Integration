"""Tests for typed Companion control entities."""

from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from bticino_companion.button import CompanionEntrypointButton, CompanionRebootButton
    from bticino_companion.api import CompanionApiClient
    from bticino_companion.coordinator import CompanionCoordinator
    from bticino_companion.dynamic_entities import DynamicEntityManager
    from bticino_companion.models import CompanionState, Diagnostics, Entrypoint, UpdateInfo
    from bticino_companion.switch import CompanionMuteSwitch, CompanionVoicemailSwitch
    from bticino_companion.update import CompanionUpdate
except ImportError as err:
    if "homeassistant" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant is not installed") from err


class _MockEntry:
    def __init__(self) -> None:
        self.entry_id = "entry-123"
        self.unique_id = "device-123"
        self.runtime_data = MagicMock()
        self.runtime_data.coordinator = MagicMock(spec=CompanionCoordinator)
        self.runtime_data.client = MagicMock()
        self.runtime_data.client.async_unlock_entrypoint = AsyncMock()
        self.runtime_data.client.async_set_muted = AsyncMock()
        self.runtime_data.client.async_set_voicemail_enabled = AsyncMock()
        self.runtime_data.client.async_install_update = AsyncMock()
        self.runtime_data.client.async_reboot = AsyncMock()

    def async_on_unload(self, listener):
        return lambda: None


def _coordinator(state: CompanionState | None = None, connected: bool = True) -> MagicMock:
    coordinator = MagicMock(spec=CompanionCoordinator)
    coordinator.data = state
    coordinator.runtime = MagicMock(connected=connected)
    return coordinator


def _state(**kwargs) -> CompanionState:
    defaults = {
        "revision": 1,
        "entrypoints": (),
        "muted": False,
        "voicemail_enabled": None,
        "update": UpdateInfo(),
        "diagnostics": Diagnostics(),
    }
    defaults.update(kwargs)
    return CompanionState(**defaults)


class TypedControlTest(unittest.IsolatedAsyncioTestCase):
    async def test_unlock_uses_typed_rest_client(self) -> None:
        entry = _MockEntry()
        coordinator = _coordinator()
        entrypoint = Entrypoint.from_dict(
            {"id": "main", "label": "Main Gate", "capabilities": {"unlock": True}, "availability": {"unlock": True}}
        )
        entity = CompanionEntrypointButton(
            entry,
            coordinator,
            entry.runtime_data.client,
            entrypoint.id,
            "Main Gate",
        )

        await entity.async_press()

        entry.runtime_data.client.async_unlock_entrypoint.assert_awaited_once_with("main")

    async def test_audio_and_voicemail_use_typed_rest_client(self) -> None:
        entry = _MockEntry()
        coordinator = _coordinator()

        mute = CompanionMuteSwitch(entry, coordinator, entry.runtime_data.client)
        voicemail = CompanionVoicemailSwitch(entry, coordinator, entry.runtime_data.client)
        await mute.async_turn_on()
        await mute.async_turn_off()
        await voicemail.async_turn_on()
        await voicemail.async_turn_off()

        entry.runtime_data.client.async_set_muted.assert_any_await(True)
        entry.runtime_data.client.async_set_muted.assert_any_await(False)
        entry.runtime_data.client.async_set_voicemail_enabled.assert_any_await(True)
        entry.runtime_data.client.async_set_voicemail_enabled.assert_any_await(False)

    async def test_update_uses_typed_rest_client(self) -> None:
        entry = _MockEntry()
        update = CompanionUpdate(entry, _coordinator(), entry.runtime_data.client)
        await update.async_install(version=None, backup=False)
        entry.runtime_data.client.async_install_update.assert_awaited_once()

    async def test_reboot_uses_typed_rest_client(self) -> None:
        entry = _MockEntry()
        reboot = CompanionRebootButton(entry, _coordinator(), entry.runtime_data.client)

        await reboot.async_press()

        entry.runtime_data.client.async_reboot.assert_awaited_once()

    async def test_reboot_client_uses_reboot_rest_endpoint(self) -> None:
        client = CompanionApiClient(MagicMock(), "http://companion", "token")
        client._async_request = AsyncMock()

        await client.async_reboot()

        client._async_request.assert_awaited_once_with("POST", "/api/v3/system/reboot", auth=True)


class _MockPlatform:
    def __init__(self) -> None:
        self.entities: dict[str, object] = {}
        self.added: list[object] = []
        self.removed: list[str] = []

    async def async_add_entities(self, entities) -> None:
        for entity in entities:
            self.added.append(entity)
            self.entities[f"entity_{len(self.entities)}"] = entity

    async def async_remove_entity(self, entity_id: str) -> None:
        self.removed.append(entity_id)
        self.entities.pop(entity_id)


class DynamicEntityManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_reconciles_entrypoint_capability_changes_and_readd(self) -> None:
        entry = _MockEntry()
        unlocked = Entrypoint.from_dict(
            {"id": "main", "label": "Main", "capabilities": {"unlock": True}, "availability": {"unlock": True}}
        )
        entry.runtime_data.coordinator = _coordinator(_state(entrypoints=(unlocked,)))
        manager = DynamicEntityManager(
            MagicMock(),
            entry,
            entry.runtime_data.coordinator,
            entry.runtime_data.client,
        )
        platform = _MockPlatform()

        await manager.async_register_platform("button", platform)

        self.assertEqual([entity.unique_id for entity in platform.added], ["device-123_unlock_main"])

        disabled = Entrypoint.from_dict(
            {"id": "main", "label": "Main", "capabilities": {"unlock": False}}
        )
        entry.runtime_data.coordinator.data = _state(entrypoints=(disabled,))
        await manager.async_reconcile()
        self.assertEqual(len(platform.removed), 1)

        entry.runtime_data.coordinator.data = _state(
            entrypoints=(
                unlocked,
            )
        )
        await manager.async_reconcile()

        self.assertEqual(
            [entity.unique_id for entity in platform.added],
            ["device-123_unlock_main", "device-123_unlock_main"],
        )

        replacement = Entrypoint.from_dict(
            {"id": "side", "label": "Side", "capabilities": {"unlock": True}}
        )
        entry.runtime_data.coordinator.data = _state(entrypoints=(replacement,))
        await manager.async_reconcile()

        self.assertEqual(len(platform.removed), 2)
        self.assertEqual(platform.added[-1].unique_id, "device-123_unlock_side")
