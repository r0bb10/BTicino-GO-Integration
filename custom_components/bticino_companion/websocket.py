"""Authenticated WebSocket connection manager for Companion v3."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import logging
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from aiohttp import ClientError, ClientSession, WSServerHandshakeError, WSMessage, WSMsgType

from .api import CompanionApiError, CompanionAuthError
from .const import (
    WEBSOCKET_CONNECT_TIMEOUT_SECONDS,
    WEBSOCKET_PATH,
    WEBSOCKET_PING_INTERVAL_SECONDS,
    WEBSOCKET_RECONNECT_MAX_SECONDS,
    WEBSOCKET_RECONNECT_MIN_SECONDS,
)
from .models import CompanionState, TraceFrame
from .protocol import ProtocolError, command_message, parse_message, ping_message

_LOGGER = logging.getLogger(__name__)

StateListener = Callable[[CompanionState], Awaitable[None]]
EventListener = Callable[[Mapping[str, Any]], Awaitable[None]]
TraceListener = Callable[[TraceFrame], Awaitable[None]]
ConnectionListener = Callable[[bool, str | None, int], Awaitable[None]]


class CompanionWebSocketError(CompanionApiError):
    """Raised for an invalid or closed Companion WebSocket connection."""


def websocket_url(base_url: str) -> str:
    """Build the v3 WebSocket URL from a Companion HTTP URL."""
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, WEBSOCKET_PATH, "", ""))


class CompanionWebSocket:
    """Maintains one authenticated push-only Companion connection."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        access_token: str,
        verify_ssl: bool,
        on_state: StateListener,
        on_event: EventListener | None = None,
        on_trace: TraceListener | None = None,
        on_connection: ConnectionListener | None = None,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token.strip()
        self._verify_ssl = verify_ssl
        self._on_state = on_state
        self._on_event = on_event
        self._on_trace = on_trace
        self._on_connection = on_connection
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._initial_state = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._websocket = None
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[Mapping[str, Any]]] = {}
        self._reconnect_attempts = 0
        self._last_error: str | None = None
        self._auth_failed = False

    @property
    def connected(self) -> bool:
        """Return whether the WebSocket is currently connected."""
        return self._connected.is_set()

    @property
    def reconnect_attempts(self) -> int:
        """Return the current consecutive reconnect count."""
        return self._reconnect_attempts

    @property
    def last_error(self) -> str | None:
        """Return the last transport error."""
        return self._last_error

    def update_runtime_config(self, *, base_url: str, access_token: str, verify_ssl: bool) -> None:
        """Update connection values used by the next reconnect."""
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token.strip()
        self._verify_ssl = verify_ssl

    async def async_start(self) -> None:
        """Start the connection task."""
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._async_run(), name="bticino_companion_websocket")

    async def async_stop(self) -> None:
        """Close the active connection and wait for the task to finish."""
        self._stop.set()
        websocket = self._websocket
        if websocket is not None:
            await websocket.close()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._connected.clear()
        self._fail_pending(CompanionWebSocketError("Companion WebSocket stopped"))

    async def async_wait_connected(self, timeout: float = WEBSOCKET_CONNECT_TIMEOUT_SECONDS) -> None:
        """Wait for the initial state push."""
        try:
            await asyncio.wait_for(self._initial_state.wait(), timeout)
        except TimeoutError as err:
            if self._auth_failed:
                raise CompanionAuthError("Companion authentication failed") from err
            raise CompanionWebSocketError("timed out connecting to Companion WebSocket") from err

    async def async_command(
        self,
        action: str,
        payload: Mapping[str, Any] | None = None,
        timeout: float = WEBSOCKET_CONNECT_TIMEOUT_SECONDS,
    ) -> Mapping[str, Any]:
        """Send a command and wait for its command_result reply."""
        if not self._connected.is_set():
            raise CompanionWebSocketError("Companion WebSocket is not connected")
        command_id = f"cmd-{uuid4().hex}"
        future: asyncio.Future[Mapping[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        try:
            await self._async_send(command_message(command_id, action, payload))
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(command_id, None)

    async def _async_run(self) -> None:
        delay = WEBSOCKET_RECONNECT_MIN_SECONDS
        while not self._stop.is_set():
            try:
                await self._async_connect_and_receive()
                raise CompanionWebSocketError("Companion WebSocket closed")
            except asyncio.CancelledError:
                raise
            except CompanionAuthError as err:
                self._last_error = str(err)
                self._auth_failed = True
                self._reconnect_attempts += 1
                await self._async_notify_connection(False)
                await self._async_wait_or_stop(WEBSOCKET_RECONNECT_MAX_SECONDS)
            except (CompanionApiError, OSError) as err:
                self._last_error = str(err)
                self._reconnect_attempts += 1
                await self._async_notify_connection(False)
                await self._async_wait_or_stop(delay)
                delay = min(delay * 2, WEBSOCKET_RECONNECT_MAX_SECONDS)
            finally:
                self._connected.clear()
                self._initial_state.clear()
                self._websocket = None
                self._fail_pending(CompanionWebSocketError("Companion WebSocket disconnected"))

    async def _async_connect_and_receive(self) -> None:
        if not self._access_token:
            raise CompanionAuthError("an access token is required")
        try:
            websocket = await self._session.ws_connect(
                websocket_url(self._base_url),
                headers={"Authorization": f"Bearer {self._access_token}"},
                ssl=self._verify_ssl,
                timeout=WEBSOCKET_CONNECT_TIMEOUT_SECONDS,
            )
        except WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise CompanionAuthError("Companion authentication failed") from err
            raise CompanionWebSocketError("unable to connect to Companion WebSocket") from err
        except (ClientError, OSError, TimeoutError) as err:
            raise CompanionWebSocketError("unable to connect to Companion WebSocket") from err

        self._websocket = websocket
        self._auth_failed = False
        self._reconnect_attempts = 0
        self._last_error = None
        self._connected.set()
        await self._async_notify_connection(True)
        await self._async_send(command_message(f"state-{uuid4().hex}", "state.get"))

        last_ping = 0.0
        while not self._stop.is_set():
            remaining = max(0.0, WEBSOCKET_PING_INTERVAL_SECONDS - (monotonic() - last_ping))
            try:
                message = await asyncio.wait_for(websocket.receive(), remaining)
            except TimeoutError:
                await self._async_send(ping_message(f"ping-{uuid4().hex}"))
                last_ping = monotonic()
                continue
            await self._async_handle_message(message)
            if monotonic() - last_ping >= WEBSOCKET_PING_INTERVAL_SECONDS:
                await self._async_send(ping_message(f"ping-{uuid4().hex}"))
                last_ping = monotonic()

    async def _async_handle_message(self, message: WSMessage) -> None:
        if message.type is WSMsgType.TEXT:
            try:
                payload = parse_message(message.data)
            except ProtocolError as err:
                raise CompanionWebSocketError(str(err)) from err
            message_type = payload["type"]
            if message_type == "state":
                body = payload.get("payload")
                if not isinstance(body, Mapping):
                    raise CompanionWebSocketError("Companion sent an invalid state payload")
                await self._on_state(CompanionState.from_dict(body))
                self._initial_state.set()
                return
            if message_type == "event":
                body = payload.get("payload")
                if isinstance(body, Mapping) and self._on_event is not None:
                    await self._on_event(body)
                return
            if message_type == "trace":
                body = payload.get("payload")
                if isinstance(body, Mapping) and self._on_trace is not None:
                    await self._on_trace(TraceFrame.from_dict(body))
                return
            if message_type == "command_result":
                self._resolve_command(payload)
                return
            if message_type == "error":
                body = payload.get("payload")
                detail = body if isinstance(body, Mapping) else {}
                if str(detail.get("code", "")).lower() == "auth_failed":
                    raise CompanionAuthError(str(detail.get("message", "Companion authentication failed")))
                _LOGGER.warning("Companion WebSocket error: %s", detail.get("message", "unknown error"))
                return
            if message_type == "pong":
                return
            raise CompanionWebSocketError(f"Companion sent unsupported WebSocket type {message_type!r}")
        if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
            raise CompanionWebSocketError("Companion WebSocket closed")
        if message.type is WSMsgType.ERROR:
            raise CompanionWebSocketError("Companion WebSocket failed") from message.data

    def _resolve_command(self, message: Mapping[str, Any]) -> None:
        command_id = message.get("id")
        if not isinstance(command_id, str):
            return
        future = self._pending.get(command_id)
        if future is None or future.done():
            return
        if message.get("ok") is True:
            payload = message.get("payload")
            future.set_result(payload if isinstance(payload, Mapping) else {})
            return
        future.set_exception(CompanionWebSocketError("Companion command failed"))

    async def _async_send(self, message: Mapping[str, Any]) -> None:
        websocket = self._websocket
        if websocket is None or websocket.closed:
            raise CompanionWebSocketError("Companion WebSocket is not connected")
        async with self._send_lock:
            await websocket.send_json(message)

    async def _async_wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), seconds)
        except TimeoutError:
            return

    async def _async_notify_connection(self, connected: bool) -> None:
        self._connected.clear() if not connected else None
        if self._on_connection is not None:
            await self._on_connection(connected, self._last_error, self._reconnect_attempts)

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
