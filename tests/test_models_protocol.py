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
