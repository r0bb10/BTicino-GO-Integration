"""Coordinator for BTicino Companion integration."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
import json
import logging
import random
import time
from typing import Any, Awaitable, Callable, TypeVar

from aiohttp import ClientConnectionError, ClientPayloadError, ClientResponse
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CompanionApiClient, CompanionApiError, CompanionAuthError
from .const import (
    COMMAND_TIMEOUT_SECONDS,
    COORDINATOR_UPDATE_INTERVAL,
    DOMAIN,
    SSE_AVAILABILITY_GRACE_SECONDS,
    SSE_HEARTBEAT_TIMEOUT_SECONDS,
    SSE_READLINE_TIMEOUT_SECONDS,
    SSE_RECONNECT_JITTER_SECONDS,
    SSE_RECONNECT_MAX_SECONDS,
    SSE_RECONNECT_MIN_SECONDS,
)

_T = TypeVar("_T")
_LOGGER = logging.getLogger(__name__)


class CompanionCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches state snapshots and keeps SSE updates flowing."""

    def __init__(self, hass, client: CompanionApiClient) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=COORDINATOR_UPDATE_INTERVAL,
        )
        self.client = client
        self._command_lock = asyncio.Lock()

        self._sse_task: asyncio.Task[None] | None = None
        self._sse_stop = asyncio.Event()
        self._sse_connected = False
        self._sse_last_error: str | None = None
        self._sse_last_activity = 0.0
        self._connected_grace_until = 0.0
        self._reconnect_attempts = 0
        self._last_event_id = 0

    @property
    def connected(self) -> bool:
        if self._sse_connected:
            return True
        if self._connected_grace_until <= 0:
            return False
        return time.monotonic() <= self._connected_grace_until

    @property
    def sse_connected(self) -> bool:
        return self._sse_connected

    @property
    def sse_last_error(self) -> str | None:
        return self._sse_last_error

    @property
    def last_event_id(self) -> int:
        return self._last_event_id

    @property
    def sse_stale(self) -> bool:
        if not self._sse_connected:
            return not self.connected
        if self._sse_last_activity <= 0:
            return False
        return (time.monotonic() - self._sse_last_activity) >= SSE_HEARTBEAT_TIMEOUT_SECONDS

    @property
    def needs_claim(self) -> bool:
        data = self.data if isinstance(self.data, dict) else {}
        auth = data.get("auth", {}) if isinstance(data, dict) else {}
        return bool((auth if isinstance(auth, dict) else {}).get("needs_claim"))

    @property
    def entities_available(self) -> bool:
        return self.connected and not self.needs_claim

    async def async_start_event_stream(self) -> None:
        """Start background SSE task if not already running."""
        if self._sse_task and not self._sse_task.done():
            return

        self._sse_stop.clear()
        create_background_task = getattr(self.hass, "async_create_background_task", None)
        if callable(create_background_task):
            self._sse_task = create_background_task(
                self._async_event_stream_loop(),
                f"{DOMAIN}_events",
            )
        else:
            self._sse_task = self.hass.async_create_task(self._async_event_stream_loop())

        if self._sse_task is not None:
            self._sse_task.add_done_callback(self._handle_sse_done)

    async def async_stop_event_stream(self) -> None:
        """Stop background SSE task."""
        self._sse_stop.set()
        task = self._sse_task
        self._sse_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        self._sse_connected = False
        self._sse_last_error = "event stream stopped"
        self._connected_grace_until = 0.0
        self._publish_runtime_state()

    async def async_restart_event_stream(self) -> None:
        """Restart SSE task with latest settings."""
        await self.async_stop_event_stream()
        await self.async_start_event_stream()

    async def async_run_command(
        self,
        *,
        label: str,
        command_coro_factory: Callable[[], Awaitable[_T]],
        timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
    ) -> _T:
        """Serialize command execution and refresh state after success."""
        async with self._command_lock:
            try:
                async with asyncio.timeout(timeout_seconds):
                    result = await command_coro_factory()
            except TimeoutError as err:
                raise CompanionApiError(f"{label} timed out") from err

            await self.async_request_refresh()
            return result

    async def _async_update_data(self) -> dict[str, Any]:
        if not self._sse_stop.is_set() and (self._sse_task is None or self._sse_task.done()):
            await self.async_start_event_stream()

        try:
            health = await self.client.async_get_health()

            auth_status: dict[str, Any]
            state: dict[str, Any] = {}
            entrypoints: dict[str, Any] = {"entrypoints": []}
            capabilities: dict[str, Any] = {"api_version": "v2", "capabilities": []}

            try:
                auth_status = await self.client.async_get_auth_status(auth=False)
            except CompanionAuthError:
                auth_status = await self.client.async_get_auth_status(auth=True)

            needs_claim = bool(auth_status.get("needs_claim"))
            if not needs_claim:
                state = await self.client.async_get_state()
                entrypoints = await self.client.async_get_entrypoints()
                capabilities = await self.client.async_get_capabilities()

        except CompanionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CompanionApiError as err:
            self._publish_runtime_state()
            raise UpdateFailed(str(err)) from err

        self._touch_connection_activity()
        existing = self.data if isinstance(self.data, dict) else {}
        runtime = self._runtime_snapshot()

        state = self._normalize_state_payload(state)

        return {
            "health": health,
            "auth": auth_status,
            "state": state,
            "entrypoints": entrypoints,
            "capabilities": capabilities,
            "last_event": existing.get("last_event"),
            "runtime": runtime,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    async def _async_event_stream_loop(self) -> None:
        reconnect_delay = SSE_RECONNECT_MIN_SECONDS
        while not self._sse_stop.is_set():
            response: ClientResponse | None = None
            try:
                response = await self.client.async_open_events_stream(
                    last_event_id=self._last_event_id if self._last_event_id > 0 else None
                )
                self._sse_connected = True
                self._sse_last_error = None
                self._touch_connection_activity()
                self._publish_runtime_state()

                if self._reconnect_attempts > 0:
                    await self.async_request_refresh()
                reconnect_delay = SSE_RECONNECT_MIN_SECONDS
                self._reconnect_attempts = 0
                await self._async_consume_sse(response)
                raise ClientPayloadError("event stream closed")
            except asyncio.CancelledError:
                raise
            except CompanionAuthError as err:
                self._sse_connected = False
                self._sse_last_error = str(err)
                self._publish_runtime_state()
                self._reconnect_attempts += 1
                await self._async_wait_or_stop(10.0)
            except (CompanionApiError, ClientConnectionError, ClientPayloadError, OSError) as err:
                self._sse_connected = False
                self._sse_last_error = str(err)
                self._publish_runtime_state()
                self._reconnect_attempts += 1
                jitter = random.uniform(0.0, SSE_RECONNECT_JITTER_SECONDS)
                await self._async_wait_or_stop(reconnect_delay + jitter)
                reconnect_delay = min(reconnect_delay * 2.0, SSE_RECONNECT_MAX_SECONDS)
            except Exception as err:  # noqa: BLE001
                self._sse_connected = False
                self._sse_last_error = f"event stream crashed: {err}"
                self._publish_runtime_state()
                _LOGGER.warning("Companion SSE loop crashed, retrying: %s", err, exc_info=True)
                self._reconnect_attempts += 1
                jitter = random.uniform(0.0, SSE_RECONNECT_JITTER_SECONDS)
                await self._async_wait_or_stop(reconnect_delay + jitter)
                reconnect_delay = min(reconnect_delay * 2.0, SSE_RECONNECT_MAX_SECONDS)
            finally:
                if response is not None:
                    response.close()

    async def _async_wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._sse_stop.wait(), timeout=seconds)
        except TimeoutError:
            return

    async def _async_consume_sse(self, response: ClientResponse) -> None:
        event_id: int | None = None
        data_lines: list[str] = []

        while not response.content.at_eof():
            try:
                raw_line = await asyncio.wait_for(
                    response.content.readline(),
                    timeout=SSE_READLINE_TIMEOUT_SECONDS,
                )
            except TimeoutError as err:
                now = time.monotonic()
                if self._sse_last_activity > 0 and (now - self._sse_last_activity) >= SSE_HEARTBEAT_TIMEOUT_SECONDS:
                    raise ClientPayloadError("SSE stream became stale") from err
                continue

            if raw_line == b"":
                break

            self._touch_connection_activity()
            line = raw_line.decode(errors="ignore").rstrip("\r\n")
            if line == "":
                await self._async_dispatch_sse_event(event_id, data_lines)
                event_id = None
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("id:"):
                raw = line[3:].strip()
                try:
                    event_id = int(raw)
                except ValueError:
                    event_id = None
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        if event_id is not None or data_lines:
            await self._async_dispatch_sse_event(event_id, data_lines)

    async def _async_dispatch_sse_event(self, event_id: int | None, data_lines: list[str]) -> None:
        if not data_lines:
            return

        raw_data = "\n".join(data_lines).strip()
        if not raw_data:
            return

        try:
            payload = json.loads(raw_data)
        except json.JSONDecodeError:
            return

        if not isinstance(payload, dict):
            return

        inferred_id = payload.get("id")
        if isinstance(inferred_id, int):
            self._last_event_id = inferred_id
        elif isinstance(event_id, int):
            self._last_event_id = event_id

        event_type = payload.get("type")
        if isinstance(event_type, str) and event_type.strip().lower() == "heartbeat":
            return

        self._apply_event(payload)

    def _apply_event(self, event: dict[str, Any]) -> None:
        existing = dict(self.data or {})
        state = existing.get("state")
        if not isinstance(state, dict):
            state = {}
        else:
            state = dict(state)

        event_type = event.get("type")
        if isinstance(event_type, str):
            self._apply_state_transition(state, event_type)

        entrypoint_id = event.get("entrypoint_id")
        if isinstance(event_type, str):
            self._apply_active_entrypoint_transition(state, event_type, entrypoint_id)

        existing["state"] = self._normalize_state_payload(state)
        existing["last_event"] = event
        existing["runtime"] = self._runtime_snapshot()
        existing["updated_at"] = datetime.now(UTC).isoformat()
        self.async_set_updated_data(existing)

    @staticmethod
    def _apply_state_transition(state: dict[str, Any], event_type: str) -> None:
        stream_active = bool(state.get("stream_active"))
        ringing = bool(state.get("ringing"))

        if event_type in ("ring.started", "call.incoming"):
            state["call_state"] = "ringing"
            state["ringing"] = True
            return

        if event_type == "ring.ended":
            state["ringing"] = False
            state["call_state"] = "active" if stream_active else "idle"
            return

        if event_type == "call.answered":
            state["call_state"] = "active"
            return

        if event_type == "call.ended":
            if stream_active:
                state["call_state"] = "active"
            elif ringing:
                state["call_state"] = "ringing"
            else:
                state["call_state"] = "idle"
            return

        if event_type == "stream.started":
            state["stream_active"] = True
            state["call_state"] = "active"
            return

        if event_type == "stream.stopped":
            state["stream_active"] = False
            state["call_state"] = "ringing" if ringing else "idle"
            return

    @staticmethod
    def _apply_active_entrypoint_transition(
        state: dict[str, Any],
        event_type: str,
        entrypoint_id: Any,
    ) -> None:
        normalized = ""
        if isinstance(entrypoint_id, str):
            normalized = entrypoint_id.strip()

        if event_type in ("ring.started", "call.incoming", "call.view_requested", "stream.started"):
            if normalized and normalized != "floor":
                state["active_entrypoint"] = normalized
            return

        if event_type == "ring.ended":
            if not bool(state.get("stream_active")):
                state["active_entrypoint"] = "none"
            return

        if event_type == "stream.stopped":
            if not bool(state.get("ringing")):
                state["active_entrypoint"] = "none"
            return

        if event_type == "call.ended":
            if not bool(state.get("stream_active")) and not bool(state.get("ringing")):
                state["active_entrypoint"] = "none"

    def _runtime_snapshot(self) -> dict[str, Any]:
        age_sec: float | None = None
        if self._sse_last_activity > 0:
            age_sec = max(0.0, time.monotonic() - self._sse_last_activity)
        grace_sec: float = 0.0
        if self._connected_grace_until > 0:
            grace_sec = max(0.0, self._connected_grace_until - time.monotonic())

        return {
            "connected": self.connected,
            "sse_connected": self._sse_connected,
            "sse_last_error": self._sse_last_error,
            "sse_last_event_id": self._last_event_id,
            "sse_last_activity_age_sec": age_sec,
            "sse_stale": self.sse_stale,
            "availability_grace_sec": grace_sec,
            "sse_reconnect_attempts": self._reconnect_attempts,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def _publish_runtime_state(self) -> None:
        if not isinstance(self.data, dict):
            return
        updated = dict(self.data)
        updated["runtime"] = self._runtime_snapshot()
        self.async_set_updated_data(updated)

    def _handle_sse_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        err = task.exception()
        if err is not None:
            self._sse_connected = False
            self._sse_last_error = f"event stream task exited with error: {err}"
            _LOGGER.warning("Companion SSE task exited with error: %s", err)
            self._publish_runtime_state()
            return

        self._sse_connected = False
        self._sse_last_error = "event stream task exited unexpectedly"
        self._publish_runtime_state()
        _LOGGER.warning("Companion SSE task exited unexpectedly")

    def _touch_connection_activity(self) -> None:
        now = time.monotonic()
        self._sse_last_activity = now
        self._connected_grace_until = now + SSE_AVAILABILITY_GRACE_SECONDS

    @staticmethod
    def _normalize_state_payload(state: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(state or {})
        active = normalized.get("active_entrypoint")
        if not isinstance(active, str) or not active.strip():
            normalized["active_entrypoint"] = "none"
        else:
            normalized["active_entrypoint"] = active.strip()
        if not isinstance(normalized.get("call_state"), str) or not str(normalized.get("call_state", "")).strip():
            normalized["call_state"] = "idle"
        normalized["stream_active"] = bool(normalized.get("stream_active", False))
        normalized["ringing"] = bool(normalized.get("ringing", False))
        return normalized
