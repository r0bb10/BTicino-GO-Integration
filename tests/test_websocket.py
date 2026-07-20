"""Tests for Companion WebSocket reconnect behavior."""

from __future__ import annotations

import sys
from pathlib import Path
from time import monotonic
import unittest
from unittest.mock import AsyncMock, MagicMock

from aiohttp import WSMessage, WSMsgType
from zeroconf.const import _TYPE_PTR as TYPE_PTR

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from bticino_companion.const import (
        WEBSOCKET_EXPECTED_DISCONNECT_COOLDOWN_SECONDS,
        WEBSOCKET_STABLE_CONNECTION_SECONDS,
    )
    from bticino_companion.websocket import ConnectionState, CompanionWebSocket, CompanionWebSocketError
except ImportError as err:
    if "homeassistant" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant is not installed") from err


class CompanionWebSocketTest(unittest.IsolatedAsyncioTestCase):
    async def test_reconnects_promptly_after_a_connection_closes(self) -> None:
        websocket = CompanionWebSocket(AsyncMock(), "http://companion", "token", "companion", AsyncMock())
        attempts = 0

        async def connect_and_close() -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise CompanionWebSocketError(f"failure {attempts}")
            websocket._stop.set()

        websocket._async_connect_and_receive = AsyncMock(side_effect=connect_and_close)
        websocket._async_wait_for_reconnect = AsyncMock()

        await websocket._async_run()

        self.assertEqual(
            [call.args[0] for call in websocket._async_wait_for_reconnect.await_args_list],
            [2],
        )

    async def test_ready_disconnect_reconnects_without_backoff(self) -> None:
        websocket = CompanionWebSocket(AsyncMock(), "http://companion", "token", "companion", AsyncMock())
        attempts = 0

        async def connect_then_close() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                websocket._state = ConnectionState.READY
                websocket._ready_at = monotonic() - WEBSOCKET_STABLE_CONNECTION_SECONDS
                raise CompanionWebSocketError("connection closed")
            websocket._stop.set()

        websocket._async_connect_and_receive = AsyncMock(side_effect=connect_then_close)
        websocket._async_wait_for_reconnect = AsyncMock()

        await websocket._async_run()

        websocket._async_wait_for_reconnect.assert_not_awaited()

    async def test_mdns_boot_advertisement_wakes_reconnect(self) -> None:
        websocket = CompanionWebSocket(AsyncMock(), "http://companion", "token", "companion", AsyncMock())
        record = type("Record", (), {"type": TYPE_PTR, "alias": "companion._bticomp._tcp.local."})()
        update = type("Update", (), {"new": record})()

        websocket.async_update_records(None, 0, [update])

        self.assertTrue(websocket._reconnect_now.is_set())

    async def test_registers_listener_on_home_assistant_zeroconf_instance(self) -> None:
        websocket = CompanionWebSocket(AsyncMock(), "http://companion", "token", "companion", AsyncMock())
        zeroconf_instance = MagicMock()
        websocket._zeroconf_instance = zeroconf_instance

        websocket._start_zeroconf_listener()
        websocket._stop_zeroconf_listener()

        zeroconf_instance.add_listener.assert_called_once_with(websocket, None)
        zeroconf_instance.remove_listener.assert_called_once_with(websocket)

    async def test_expected_disconnect_uses_reboot_cooldown(self) -> None:
        websocket = CompanionWebSocket(AsyncMock(), "http://companion", "token", "companion", AsyncMock())
        attempts = 0

        async def connect_then_close() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                websocket._state = ConnectionState.READY
                raise CompanionWebSocketError("connection closed")
            websocket._stop.set()

        websocket.async_expect_disconnect()
        websocket._async_connect_and_receive = AsyncMock(side_effect=connect_then_close)
        websocket._async_wait_for_reconnect = AsyncMock()

        await websocket._async_run()

        websocket._async_wait_for_reconnect.assert_awaited_once_with(
            WEBSOCKET_EXPECTED_DISCONNECT_COOLDOWN_SECONDS
        )

    async def test_valid_inbound_event_refreshes_heartbeat(self) -> None:
        websocket = CompanionWebSocket(AsyncMock(), "http://companion", "token", "companion", AsyncMock())

        alive = await websocket._async_handle_message(
            WSMessage(WSMsgType.TEXT, '{"type":"event","payload":{}}', "")
        )

        self.assertTrue(alive)
