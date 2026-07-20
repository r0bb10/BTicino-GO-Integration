"""Persistent Companion state transport with ESPHome-style reconnect behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
import logging
from time import monotonic
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from aiohttp import ClientError, ClientSession, WSServerHandshakeError, WSMessage, WSMsgType
from zeroconf import RecordUpdate, RecordUpdateListener, Zeroconf
from zeroconf.const import _TYPE_A as TYPE_A, _TYPE_AAAA as TYPE_AAAA, _TYPE_PTR as TYPE_PTR

from .api import CompanionApiError, CompanionAuthError
from .const import (
    WEBSOCKET_CONNECT_TIMEOUT_SECONDS,
    WEBSOCKET_CLOSE_TIMEOUT_SECONDS,
    WEBSOCKET_EXPECTED_DISCONNECT_COOLDOWN_SECONDS,
    WEBSOCKET_PATH,
    WEBSOCKET_PING_INTERVAL_SECONDS,
    WEBSOCKET_PONG_TIMEOUT_SECONDS,
    WEBSOCKET_RECONNECT_MAX_SECONDS,
    WEBSOCKET_STABLE_CONNECTION_SECONDS,
)
from .models import CompanionState, TraceFrame
from .protocol import ProtocolError, parse_message, ping_message

_LOGGER = logging.getLogger(__name__)

StateListener = Callable[[CompanionState], Awaitable[None]]
EventListener = Callable[[Mapping[str, Any]], Awaitable[None]]
TraceListener = Callable[[TraceFrame], Awaitable[None]]
ConnectionListener = Callable[[bool, str | None, int], Awaitable[None]]


class CompanionWebSocketError(CompanionApiError):
    """Raised for an invalid or closed Companion state connection."""


class CompanionReconnectRequested(CompanionWebSocketError):
    """Raised when a Companion boot advertisement supersedes a stale connect."""


class ConnectionState(Enum):
    """Lifecycle of the persistent Companion state connection."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    READY = "ready"


def websocket_url(base_url: str) -> str:
    """Build the state WebSocket URL from a Companion HTTP URL."""
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, WEBSOCKET_PATH, "", ""))


class CompanionWebSocket(RecordUpdateListener):
    """Maintain Companion state with immediate reconnects on boot advertisement."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        access_token: str,
        device_id: str,
        on_state: StateListener,
        on_event: EventListener | None = None,
        on_trace: TraceListener | None = None,
        on_connection: ConnectionListener | None = None,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token.strip()
        self._device_id = device_id.strip().lower()
        self._on_state = on_state
        self._on_event = on_event
        self._on_trace = on_trace
        self._on_connection = on_connection
        self._stop = asyncio.Event()
        self._reconnect_now = asyncio.Event()
        self._connected = asyncio.Event()
        self._initial_state = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._websocket = None
        self._send_lock = asyncio.Lock()
        self._reconnect_attempts = 0
        self._last_error: str | None = None
        self._auth_failed = False
        self._expected_disconnect = False
        self._state = ConnectionState.DISCONNECTED
        self._ready_at: float | None = None
        self._zeroconf_instance: Zeroconf | None = None
        self._zeroconf_listening = False
        self._service_name = f"{self._device_id}._bticomp._tcp.local."
        self._host_name = f"{self._device_id}.local."

    @property
    def connected(self) -> bool:
        """Return whether the initial state has completed for this connection."""
        return self._connected.is_set()

    @property
    def reconnect_attempts(self) -> int:
        """Return consecutive failed connection attempts."""
        return self._reconnect_attempts

    @property
    def last_error(self) -> str | None:
        """Return the most recent connection error."""
        return self._last_error

    async def async_start(self, zeroconf_instance: Zeroconf | None = None) -> None:
        """Start reconnect management and optionally attach HA's mDNS instance."""
        if self._task is not None and not self._task.done():
            return
        self._zeroconf_instance = zeroconf_instance
        self._stop.clear()
        self._reconnect_now.set()
        self._task = asyncio.create_task(self._async_run(), name="bticino_companion_state")

    async def async_stop(self) -> None:
        """Stop reconnect management and close the active socket."""
        self._stop.set()
        self._reconnect_now.set()
        self._stop_zeroconf_listener()
        websocket = self._websocket
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if websocket is not None:
            try:
                await asyncio.wait_for(websocket.close(), WEBSOCKET_CLOSE_TIMEOUT_SECONDS)
            except TimeoutError:
                _LOGGER.debug("Timed out closing Companion state socket")
        self._set_disconnected()

    async def async_update_base_url(self, base_url: str) -> None:
        """Reconnect immediately after a discovered Companion address changes."""
        base_url = base_url.rstrip("/")
        if base_url == self._base_url:
            return
        self._base_url = base_url
        self._reconnect_now.set()
        _LOGGER.debug("Companion address updated; reconnecting state transport")
        if websocket := self._websocket:
            try:
                await asyncio.wait_for(websocket.close(), WEBSOCKET_CLOSE_TIMEOUT_SECONDS)
            except TimeoutError:
                _LOGGER.debug("Timed out closing stale Companion state socket after address update")

    def async_expect_disconnect(self) -> None:
        """Mark the next ready-session close as an expected Companion reboot."""
        self._expected_disconnect = True

    def async_cancel_expected_disconnect(self) -> None:
        """Clear a reboot expectation when the reboot command was rejected."""
        self._expected_disconnect = False

    async def async_wait_connected(self, timeout: float = WEBSOCKET_CONNECT_TIMEOUT_SECONDS) -> None:
        """Wait until the authenticated connection receives its initial state."""
        try:
            await asyncio.wait_for(self._initial_state.wait(), timeout)
        except TimeoutError as err:
            if self._auth_failed:
                raise CompanionAuthError("Companion authentication failed") from err
            raise CompanionWebSocketError("timed out connecting to Companion state transport") from err

    async def _async_run(self) -> None:
        delay = 0.0
        while not self._stop.is_set():
            if delay:
                await self._async_wait_for_reconnect(delay)
                if self._stop.is_set():
                    return
            self._reconnect_now.clear()
            try:
                await self._async_connect_and_receive()
                raise CompanionWebSocketError("Companion state socket closed")
            except asyncio.CancelledError:
                raise
            except CompanionAuthError as err:
                self._auth_failed = True
                self._last_error = str(err)
                self._reconnect_attempts = int(WEBSOCKET_RECONNECT_MAX_SECONDS)
                delay = WEBSOCKET_RECONNECT_MAX_SECONDS
            except CompanionReconnectRequested:
                delay = 0.0
                _LOGGER.debug("Restarting stale Companion state connection after mDNS advertisement")
            except (CompanionApiError, OSError) as err:
                self._last_error = str(err)
                was_ready = self._state is ConnectionState.READY
                expected_disconnect = self._expected_disconnect
                self._expected_disconnect = False
                stable_connection = bool(
                    self._ready_at is not None
                    and monotonic() - self._ready_at >= WEBSOCKET_STABLE_CONNECTION_SECONDS
                )
                if was_ready and expected_disconnect:
                    delay = WEBSOCKET_EXPECTED_DISCONNECT_COOLDOWN_SECONDS
                    self._reconnect_attempts = 0
                elif was_ready and stable_connection:
                    delay = 0.0
                    self._reconnect_attempts = 0
                else:
                    self._reconnect_attempts += 1
                    delay = min(round(1.8**self._reconnect_attempts), WEBSOCKET_RECONNECT_MAX_SECONDS)
                _LOGGER.debug(
                    "Companion state transport disconnected: %s; expected=%s; stable=%s; reconnecting in %.0fs",
                    err,
                    expected_disconnect,
                    stable_connection,
                    delay,
                )
            finally:
                self._set_disconnected()
                self._websocket = None

    async def _async_connect_and_receive(self) -> None:
        if not self._access_token:
            raise CompanionAuthError("an access token is required")
        self._state = ConnectionState.CONNECTING
        self._start_zeroconf_listener()
        try:
            websocket = await self._async_connect()
        except WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise CompanionAuthError("Companion authentication failed") from err
            raise CompanionWebSocketError("unable to connect to Companion state transport") from err
        except (ClientError, OSError, TimeoutError) as err:
            raise CompanionWebSocketError("unable to connect to Companion state transport") from err

        self._websocket = websocket
        last_ping = monotonic()
        last_pong = last_ping
        while not self._stop.is_set():
            now = monotonic()
            next_ping = last_ping + WEBSOCKET_PING_INTERVAL_SECONDS
            pong_deadline = last_pong + WEBSOCKET_PONG_TIMEOUT_SECONDS
            timeout = max(0.0, min(next_ping, pong_deadline) - now)
            try:
                message = await asyncio.wait_for(websocket.receive(), timeout)
            except TimeoutError:
                now = monotonic()
                if now >= pong_deadline:
                    raise CompanionWebSocketError("Companion state heartbeat timed out")
                await self._async_send(ping_message(f"ping-{uuid4().hex}"))
                last_ping = now
                continue
            if await self._async_handle_message(message):
                last_pong = monotonic()

    async def _async_connect(self):
        connect_task = asyncio.ensure_future(
            self._session.ws_connect(
                websocket_url(self._base_url),
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        )
        reconnect_task = asyncio.create_task(self._reconnect_now.wait())
        try:
            done, _ = await asyncio.wait(
                {connect_task, reconnect_task},
                timeout=WEBSOCKET_CONNECT_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if reconnect_task in done:
                if connect_task in done:
                    stale_websocket = connect_task.result()
                    try:
                        await asyncio.wait_for(stale_websocket.close(), WEBSOCKET_CLOSE_TIMEOUT_SECONDS)
                    except TimeoutError:
                        _LOGGER.debug("Timed out closing stale Companion state socket after mDNS wake-up")
                raise CompanionReconnectRequested("Companion boot advertisement received")
            if connect_task in done:
                return connect_task.result()
            raise TimeoutError
        finally:
            connect_task.cancel()
            reconnect_task.cancel()
            await asyncio.gather(connect_task, reconnect_task, return_exceptions=True)

    async def _async_handle_message(self, message: WSMessage) -> bool:
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
                self._set_ready()
                return True
            if message_type == "event":
                body = payload.get("payload")
                if isinstance(body, Mapping) and self._on_event is not None:
                    await self._on_event(body)
                return True
            if message_type == "trace":
                body = payload.get("payload")
                if isinstance(body, Mapping) and self._on_trace is not None:
                    await self._on_trace(TraceFrame.from_dict(body))
                return True
            if message_type == "error":
                body = payload.get("payload")
                detail = body if isinstance(body, Mapping) else {}
                if str(detail.get("code", "")).lower() == "auth_failed":
                    raise CompanionAuthError(str(detail.get("message", "Companion authentication failed")))
                _LOGGER.warning("Companion state socket error: %s", detail.get("message", "unknown error"))
                return True
            if message_type == "pong":
                return True
            raise CompanionWebSocketError(f"Companion sent unsupported WebSocket type {message_type!r}")
        if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING):
            raise CompanionWebSocketError("Companion state socket closed")
        if message.type is WSMsgType.PONG:
            return True
        if message.type is WSMsgType.ERROR:
            raise CompanionWebSocketError("Companion state socket failed") from message.data
        return False

    async def _async_send(self, message: Mapping[str, Any]) -> None:
        websocket = self._websocket
        if websocket is None or websocket.closed:
            raise CompanionWebSocketError("Companion state socket is not connected")
        async with self._send_lock:
            await websocket.send_json(message)

    async def _async_wait_for_reconnect(self, seconds: float) -> None:
        stop_wait = asyncio.create_task(self._stop.wait())
        reconnect_wait = asyncio.create_task(self._reconnect_now.wait())
        try:
            await asyncio.wait(
                {stop_wait, reconnect_wait},
                timeout=seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            stop_wait.cancel()
            reconnect_wait.cancel()
            await asyncio.gather(stop_wait, reconnect_wait, return_exceptions=True)

    def _set_ready(self) -> None:
        if self._state is ConnectionState.READY:
            return
        self._state = ConnectionState.READY
        self._ready_at = monotonic()
        self._auth_failed = False
        self._reconnect_attempts = 0
        self._last_error = None
        self._connected.set()
        self._initial_state.set()
        self._stop_zeroconf_listener()
        _LOGGER.debug("Companion state transport ready")
        if self._on_connection is not None:
            asyncio.ensure_future(self._on_connection(True, None, 0))

    def _set_disconnected(self) -> None:
        was_ready = self._state is ConnectionState.READY
        self._state = ConnectionState.DISCONNECTED
        self._ready_at = None
        self._connected.clear()
        self._initial_state.clear()
        if not self._stop.is_set():
            self._start_zeroconf_listener()
        if was_ready and self._on_connection is not None:
            asyncio.ensure_future(
                self._on_connection(False, self._last_error, self._reconnect_attempts)
            )

    def _start_zeroconf_listener(self) -> None:
        if self._zeroconf_listening or self._zeroconf_instance is None or not self._device_id:
            return
        try:
            self._zeroconf_instance.add_listener(self, None)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unable to start Companion mDNS reconnect listener: %s", err)
            return
        self._zeroconf_listening = True
        _LOGGER.debug("Listening for Companion mDNS boot advertisements")

    def _stop_zeroconf_listener(self) -> None:
        if not self._zeroconf_listening:
            return
        if self._zeroconf_instance is not None:
            self._zeroconf_instance.remove_listener(self)
        self._zeroconf_listening = False

    def async_update_records(
        self,
        zc: Zeroconf,  # noqa: ARG002
        now: float,  # noqa: ARG002
        records: list[RecordUpdate],
    ) -> None:
        """Wake a pending reconnect when this Companion advertises after boot."""
        if self._state is ConnectionState.READY or self._stop.is_set():
            return
        for record_update in records:
            record = record_update.new
            if (
                record.type == TYPE_PTR
                and getattr(record, "alias", "").lower() == self._service_name
            ) or (
                record.type in (TYPE_A, TYPE_AAAA)
                and record.name.lower() == self._host_name
            ):
                _LOGGER.debug("Companion mDNS advertisement received; reconnecting now")
                self._stop_zeroconf_listener()
                self._reconnect_now.set()
                return
