"""Tests for Companion WebSocket reconnect behavior."""

from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from bticino_companion.const import WEBSOCKET_RECONNECT_MIN_SECONDS
    from bticino_companion.websocket import CompanionWebSocket, CompanionWebSocketError
except ImportError as err:
    if "homeassistant" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant is not installed") from err


class CompanionWebSocketTest(unittest.IsolatedAsyncioTestCase):
    async def test_reconnects_promptly_after_a_connection_closes(self) -> None:
        websocket = CompanionWebSocket(AsyncMock(), "http://companion", "token", AsyncMock())
        attempts = 0

        async def connect_and_close() -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise CompanionWebSocketError(f"failure {attempts}")
            websocket._stop.set()

        websocket._async_connect_and_receive = AsyncMock(side_effect=connect_and_close)
        websocket._async_wait_or_stop = AsyncMock()

        await websocket._async_run()

        self.assertEqual(
            [call.args[0] for call in websocket._async_wait_or_stop.await_args_list],
            [
                WEBSOCKET_RECONNECT_MIN_SECONDS,
                WEBSOCKET_RECONNECT_MIN_SECONDS * 2,
                WEBSOCKET_RECONNECT_MIN_SECONDS,
            ],
        )
