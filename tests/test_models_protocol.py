"""Tests for pure v3 models and WebSocket protocol helpers."""

from __future__ import annotations

import sys
from pathlib import Path
import unittest

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components" / "bticino_companion"
sys.path.insert(0, str(COMPONENT_PATH))

from models import CompanionState, Entrypoint, TraceFrame
from protocol import ProtocolError, command_message, parse_message, ping_message


class ModelsTest(unittest.TestCase):
    def test_state_prefers_active_entrypoint(self) -> None:
        state = CompanionState.from_dict(
            {
                "revision": 4,
                "call_state": "active",
                "physical_ring": {"entrypoint_id": "gate"},
                "active_call": {"dialog_id": "dialog-1", "entrypoint_id": "front"},
            }
        )

        self.assertEqual(state.revision, 4)
        self.assertEqual(state.active_entrypoint_id, "front")
        self.assertEqual(state.active_dialog_id, "dialog-1")

    def test_entrypoint_and_trace_defaults_are_safe(self) -> None:
        entrypoint = Entrypoint.from_dict({"id": "main", "capabilities": {"unlock": True}})
        trace = TraceFrame.from_dict({"direction": "rx", "frame": "*7*300##"})

        self.assertEqual(entrypoint.label, "")
        self.assertTrue(entrypoint.capabilities.unlock)
        self.assertFalse(entrypoint.capabilities.stream)
        self.assertEqual(trace.frame, "*7*300##")

    def test_state_projects_platform_capabilities(self) -> None:
        state = CompanionState.from_dict(
            {
                "entrypoints": [
                    {
                        "id": "main",
                        "label": "Main Gate",
                        "devaddr": "20",
                        "capabilities": {"stream": True, "unlock": True, "ring": True},
                    },
                    {"id": "disabled", "capabilities": {"stream": False}},
                ],
                "audio": {"muted": True},
                "voicemail": {"enabled": False},
                "system_control": {
                    "reboot_enabled": True,
                    "services": {"dropbear": {"enabled": True, "exposed": True}},
                    "update": {
                        "enabled": True,
                        "exposed": True,
                        "current_version": "3.0.0",
                        "latest_version": "3.1.0",
                        "staged_version": "3.1.0",
                        "restart_required": True,
                        "stage": "staged",
                    },
                },
                "device": {"model": "C300X", "firmware": "1.2.3", "hardware": "rev-a"},
            }
        )

        self.assertEqual([entrypoint.id for entrypoint in state.entrypoints], ["main", "disabled"])
        self.assertTrue(state.entrypoints[0].capabilities.unlock)
        self.assertTrue(state.muted)
        self.assertFalse(state.voicemail_enabled)
        self.assertTrue(state.reboot_enabled)
        self.assertEqual(state.services[0].name, "dropbear")
        self.assertEqual(state.update.installed_version, "3.0.0")
        self.assertEqual(state.update.latest_version, "3.1.0")
        self.assertEqual(state.update.staged_version, "3.1.0")
        self.assertTrue(state.update.restart_required)
        self.assertEqual(state.model, "C300X")

    def test_state_uses_pushed_diagnostics_metadata(self) -> None:
        state = CompanionState.from_dict(
            {
                "device": {"firmware": "stale", "hardware": "stale"},
                "diagnostics": {
                    "openwebnet": {
                        "ip": "192.0.2.10",
                        "mac": "00:11:22:33:44:55",
                        "firmware": "2.3.4",
                        "hardware": "rev-b",
                    },
                    "local": {"wifi_strength": 72},
                },
            }
        )

        self.assertEqual(state.diagnostics.ip_address, "192.0.2.10")
        self.assertEqual(state.diagnostics.mac_address, "00:11:22:33:44:55")
        self.assertEqual(state.diagnostics.wifi_strength, 72)
        self.assertEqual(state.firmware, "2.3.4")
        self.assertEqual(state.hardware, "rev-b")


class ProtocolTest(unittest.TestCase):
    def test_command_and_ping_messages(self) -> None:
        self.assertEqual(
            command_message("cmd-1", "entrypoint.unlock", {"entrypoint_id": "main"}),
            {
                "type": "command",
                "id": "cmd-1",
                "action": "entrypoint.unlock",
                "payload": {"entrypoint_id": "main"},
            },
        )
        self.assertEqual(ping_message("ping-1"), {"type": "ping", "id": "ping-1"})

    def test_parse_message_rejects_invalid_envelopes(self) -> None:
        self.assertEqual(parse_message('{"type":"state","payload":{}}')["type"], "state")
        with self.assertRaises(ProtocolError):
            parse_message("not json")
        with self.assertRaises(ProtocolError):
            parse_message("[]")
