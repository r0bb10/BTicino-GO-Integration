"""Tests for platform entities command payloads and availability."""

from __future__ import annotations

import base64
import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from bticino_companion.button import (
        CompanionCallButton,
        CompanionCommandButton,
        CompanionEntrypointButton,
        async_setup_entry as button_async_setup_entry,
    )
    from bticino_companion.camera import CompanionCamera, async_setup_entry as camera_async_setup_entry
    from bticino_companion.coordinator import CompanionCoordinator
    from bticino_companion.api import CompanionApiClient
    from bticino_companion.models import CompanionState, Diagnostics, Entrypoint, UpdateInfo
    from bticino_companion.sensor import async_setup_entry as sensor_async_setup_entry
    from bticino_companion.switch import CompanionMuteSwitch, CompanionVoicemailSwitch
    from bticino_companion.update import CompanionUpdate
except ImportError as err:
    if "homeassistant" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant is not installed") from err



class _MockEntry:
    """Minimal ConfigEntry stand-in."""

    def __init__(self) -> None:
        self.entry_id = "entry-123"
        self.unique_id = "device-123"
        self.runtime_data = MagicMock()
        self.runtime_data.coordinator = MagicMock(spec=CompanionCoordinator)
        self.runtime_data.client = MagicMock()
        self.runtime_data.client.base_url = "http://companion.local:8080"
        self.runtime_data.client.session = MagicMock()
        self.runtime_data.client.verify_ssl = False

    def async_on_unload(self, listener):
        return lambda: None


def _make_coordinator(state: CompanionState | None = None, connected: bool = True) -> MagicMock:
    coordinator = MagicMock(spec=CompanionCoordinator)
    coordinator.data = state
    coordinator.runtime = MagicMock()
    coordinator.runtime.connected = connected
    coordinator.async_command = AsyncMock(return_value={})
    return coordinator


def _state(**kwargs) -> CompanionState:
    defaults = {
        "revision": 1,
        "call_state": "idle",
        "active_entrypoint_id": None,
        "incoming_dialog_id": None,
        "active_dialog_id": None,
        "preview_stream_id": None,
        "entrypoints": (),
        "muted": False,
        "voicemail_enabled": None,
        "reboot_enabled": False,
        "services": (),
        "update": UpdateInfo(),
        "model": None,
        "firmware": None,
        "hardware": None,
        "diagnostics": Diagnostics(),
    }
    defaults.update(kwargs)
    return CompanionState(**defaults)


class ButtonCommandPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_unlock_sends_entrypoint_unlock_payload(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator()
        entrypoint = Entrypoint.from_dict({"id": "main", "label": "Main Gate", "capabilities": {"unlock": True}})
        entity = CompanionEntrypointButton(
            entry, coordinator, entrypoint, "unlock_main", "Unlock Main Gate", "mdi:door-open", "entrypoint.unlock"
        )
        await entity.async_press()
        coordinator.async_command.assert_awaited_once_with("entrypoint.unlock", {"entrypoint_id": "main"})

    async def test_stream_sends_entrypoint_stream_payload(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator()
        entrypoint = Entrypoint.from_dict({"id": "cam1", "capabilities": {"stream": True}})
        entity = CompanionEntrypointButton(
            entry, coordinator, entrypoint, "stream_cam1", "Stream Cam1", "mdi:video", "entrypoint.stream"
        )
        await entity.async_press()
        coordinator.async_command.assert_awaited_once_with("entrypoint.stream", {"entrypoint_id": "cam1"})

    async def test_snapshot_sends_entrypoint_snapshot_payload(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator()
        entrypoint = Entrypoint.from_dict({"id": "cam1", "capabilities": {"stream": True}})
        entity = CompanionEntrypointButton(
            entry, coordinator, entrypoint, "snapshot_cam1", "Snapshot Cam1", "mdi:camera", "entrypoint.snapshot"
        )
        await entity.async_press()
        coordinator.async_command.assert_awaited_once_with("entrypoint.snapshot", {"entrypoint_id": "cam1"})

    async def test_reboot_sends_system_reboot(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator()
        entity = CompanionCommandButton(entry, coordinator, "reboot", "Reboot", "mdi:restart-alert", "system.reboot")
        await entity.async_press()
        coordinator.async_command.assert_awaited_once_with("system.reboot", {})

    async def test_service_restart_sends_payload(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator()
        entity = CompanionCommandButton(
            entry,
            coordinator,
            "restart_dropbear",
            "Restart Dropbear",
            "mdi:restart",
            "system.service_restart",
            {"service": "dropbear"},
        )
        await entity.async_press()
        coordinator.async_command.assert_awaited_once_with("system.service_restart", {"service": "dropbear"})


class CallButtonTest(unittest.IsolatedAsyncioTestCase):
    async def test_answer_sends_dialog_id(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator(_state(call_state="incoming", incoming_dialog_id="dlg-1"))
        entity = CompanionCallButton(
            entry, coordinator, "call_answer", "Answer", "mdi:phone", "call.answer", "dlg-1"
        )
        await entity.async_press()
        coordinator.async_command.assert_awaited_once_with("call.answer", {"dialog_id": "dlg-1"})

    async def test_decline_sends_dialog_id(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator(_state(call_state="incoming", incoming_dialog_id="dlg-2"))
        entity = CompanionCallButton(
            entry, coordinator, "call_decline", "Decline", "mdi:phone-hangup", "call.decline", "dlg-2"
        )
        await entity.async_press()
        coordinator.async_command.assert_awaited_once_with("call.decline", {"dialog_id": "dlg-2"})

    async def test_hangup_sends_dialog_id(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator(_state(call_state="active", active_dialog_id="dlg-3"))
        entity = CompanionCallButton(
            entry, coordinator, "call_hangup", "Hangup", "mdi:phone-off", "call.hangup", "dlg-3"
        )
        await entity.async_press()
        coordinator.async_command.assert_awaited_once_with("call.hangup", {"dialog_id": "dlg-3"})


class SwitchCommandPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_mute_sends_audio_mute(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator()
        entity = CompanionMuteSwitch(entry, coordinator)
        await entity.async_turn_on()
        coordinator.async_command.assert_awaited_once_with("audio.mute")

    async def test_unmute_sends_audio_unmute(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator()
        entity = CompanionMuteSwitch(entry, coordinator)
        await entity.async_turn_off()
        coordinator.async_command.assert_awaited_once_with("audio.unmute")

    async def test_voicemail_enable(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator()
        entity = CompanionVoicemailSwitch(entry, coordinator)
        await entity.async_turn_on()
        coordinator.async_command.assert_awaited_once_with("voicemail.enable")

    async def test_voicemail_disable(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator()
        entity = CompanionVoicemailSwitch(entry, coordinator)
        await entity.async_turn_off()
        coordinator.async_command.assert_awaited_once_with("voicemail.disable")


class UpdateCommandPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_install_requests_server_owned_update(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator()
        entity = CompanionUpdate(entry, coordinator)
        await entity.async_install(version=None, backup=False)
        self.assertEqual(
            coordinator.async_command.await_args_list,
            [unittest.mock.call("system.update.install")],
        )


class RepairApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_repair_requests_use_authenticated_v3_endpoints(self) -> None:
        client = CompanionApiClient(MagicMock(), "http://companion.local:8080", "token")
        client._async_request = AsyncMock(return_value={"repair_code": "a1b2-c3d4"})

        await client.async_issue_repair_code()
        client._async_request.assert_awaited_once_with("POST", "/api/v3/admin/issue-repair-code", auth=True)

        client._async_request.reset_mock()
        await client.async_reset_claim("a1b2-c3d4")
        client._async_request.assert_awaited_once_with(
            "POST", "/api/v3/admin/reset-claim", auth=True, json_body={"repair_code": "a1b2-c3d4"}
        )


class CameraCommandPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_camera_image_requests_entrypoint_snapshot(self) -> None:
        entry = _MockEntry()
        image_bytes = b"jpeg-data"
        coordinator = _make_coordinator()
        coordinator.async_command = AsyncMock(return_value={"data": base64.b64encode(image_bytes).decode("ascii")})
        entrypoint = Entrypoint.from_dict({"id": "cam1", "capabilities": {"stream": True}})
        entity = CompanionCamera(entry, coordinator, entry.runtime_data.client, entrypoint)
        result = await entity.async_camera_image()
        coordinator.async_command.assert_awaited_once_with("entrypoint.snapshot", {"entrypoint_id": "cam1"})
        self.assertEqual(result, image_bytes)

    async def test_camera_image_fetches_url_when_no_data(self) -> None:
        entry = _MockEntry()
        image_bytes = b"url-jpeg"
        coordinator = _make_coordinator()
        coordinator.async_command = AsyncMock(return_value={"url": "http://companion.local/snap.jpg"})
        response_mock = MagicMock()
        response_mock.status = 200
        response_mock.read = AsyncMock(return_value=image_bytes)
        response_cm = MagicMock()
        response_cm.__aenter__ = AsyncMock(return_value=response_mock)
        response_cm.__aexit__ = AsyncMock(return_value=False)
        entry.runtime_data.client.session.get = MagicMock(return_value=response_cm)
        entrypoint = Entrypoint.from_dict({"id": "cam1", "capabilities": {"stream": True}})
        entity = CompanionCamera(entry, coordinator, entry.runtime_data.client, entrypoint)
        result = await entity.async_camera_image()
        self.assertEqual(result, image_bytes)
        entry.runtime_data.client.session.get.assert_called_once_with(
            "http://companion.local/snap.jpg", ssl=False
        )


class EntityAvailabilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_button_available_when_connected(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator(connected=True)
        entity = CompanionCommandButton(entry, coordinator, "reboot", "Reboot", "mdi:restart", "system.reboot")
        self.assertTrue(entity.available)

    async def test_button_unavailable_when_disconnected(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator(connected=False)
        entity = CompanionCommandButton(entry, coordinator, "reboot", "Reboot", "mdi:restart", "system.reboot")
        self.assertFalse(entity.available)

    async def test_call_answer_available_on_incoming_call(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator(_state(call_state="incoming", incoming_dialog_id="dlg-1"))
        entity = CompanionCallButton(
            entry, coordinator, "call_answer", "Answer", "mdi:phone", "call.answer", "dlg-1"
        )
        self.assertTrue(entity.available)

    async def test_call_answer_unavailable_when_no_incoming_call(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator(_state(call_state="idle"))
        entity = CompanionCallButton(
            entry, coordinator, "call_answer", "Answer", "mdi:phone", "call.answer", "dlg-1"
        )
        self.assertFalse(entity.available)

    async def test_call_hangup_available_on_active_call(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator(_state(call_state="active", active_dialog_id="dlg-3"))
        entity = CompanionCallButton(
            entry, coordinator, "call_hangup", "Hangup", "mdi:phone-off", "call.hangup", "dlg-3"
        )
        self.assertTrue(entity.available)

    async def test_update_available_when_exposed(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator(
            _state(update=UpdateInfo(enabled=True, exposed=True, installed_version="1.0", latest_version="2.0"))
        )
        entity = CompanionUpdate(entry, coordinator)
        self.assertTrue(entity.available)

    async def test_update_unavailable_when_not_exposed(self) -> None:
        entry = _MockEntry()
        coordinator = _make_coordinator(_state(update=UpdateInfo(enabled=True, exposed=False)))
        entity = CompanionUpdate(entry, coordinator)
        self.assertFalse(entity.available)


class SetupEntryTest(unittest.IsolatedAsyncioTestCase):
    async def test_button_setup_creates_expected_entities(self) -> None:
        entry = _MockEntry()
        state = _state(
            call_state="incoming",
            incoming_dialog_id="dlg-1",
            active_dialog_id="dlg-2",
            reboot_enabled=True,
            entrypoints=(
                Entrypoint.from_dict({"id": "main", "label": "Main", "capabilities": {"unlock": True, "stream": True}}),
            ),
            services=(type("S", (), {"name": "dropbear", "enabled": True, "exposed": True})(),),
            update=UpdateInfo(enabled=True, exposed=True, installed_version="1.0", latest_version="2.0"),
        )
        entry.runtime_data.coordinator = _make_coordinator(state)
        added: list = []

        def add_entities(entities):
            added.extend(entities)

        await button_async_setup_entry(MagicMock(), entry, add_entities)
        ids = {e.unique_id for e in added}
        self.assertIn("device-123_unlock_main", ids)
        self.assertIn("device-123_stream_main", ids)
        self.assertIn("device-123_snapshot_main", ids)
        self.assertIn("device-123_answer", ids)
        self.assertIn("device-123_decline", ids)
        self.assertIn("device-123_hangup", ids)
        self.assertIn("device-123_reboot", ids)
        self.assertIn("device-123_restart_dropbear", ids)
        self.assertNotIn("device-123_rollback", ids)

    async def test_camera_setup_creates_stream_cameras(self) -> None:
        entry = _MockEntry()
        state = _state(
            entrypoints=(
                Entrypoint.from_dict({"id": "cam1", "label": "Front", "capabilities": {"stream": True}}),
                Entrypoint.from_dict({"id": "plain", "capabilities": {"stream": False}}),
            )
        )
        entry.runtime_data.coordinator = _make_coordinator(state)
        added: list = []

        def add_entities(entities):
            added.extend(entities)

        await camera_async_setup_entry(MagicMock(), entry, add_entities)
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].unique_id, "device-123_camera_cam1")

    async def test_sensor_setup_restores_three_diagnostic_sensors(self) -> None:
        entry = _MockEntry()
        entry.runtime_data.coordinator = _make_coordinator(_state())
        added: list = []

        await sensor_async_setup_entry(MagicMock(), entry, added.extend)

        diagnostic_ids = {entity.unique_id for entity in added if "call_state" not in entity.unique_id and "active_entrypoint" not in entity.unique_id}
        self.assertEqual(diagnostic_ids, {"entry-123_ip_address", "entry-123_mac_address", "entry-123_wifi_strength"})
        self.assertTrue(all(entity.entity_category is not None for entity in added if entity.unique_id in diagnostic_ids))


if __name__ == "__main__":
    unittest.main()
