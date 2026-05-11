"""OpenWebNet trace relay from companion SSE into Home Assistant events."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
from typing import Any

from aiohttp import ClientConnectionError, ClientPayloadError, ClientResponse
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api import CompanionApiClient, CompanionApiError, CompanionAuthError
from .const import DOMAIN, EVENT_OPENWEBNET_FRAME, SIGNAL_OPENWEBNET_TRACE

_LOGGER = logging.getLogger(__name__)


def trace_signal(entry_id: str) -> str:
    return f"{SIGNAL_OPENWEBNET_TRACE}_{entry_id}"


class OpenWebNetTraceRelay:
    """Keep companion OpenWebNet trace SSE stream alive and relay payloads."""

    def __init__(self, hass: HomeAssistant, client: CompanionApiClient, entry_id: str) -> None:
        self.hass = hass
        self.client = client
        self.entry_id = entry_id
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_event_id = 0

    async def async_start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        create_background_task = getattr(self.hass, "async_create_background_task", None)
        if callable(create_background_task):
            self._task = create_background_task(
                self._async_loop(),
                f"{DOMAIN}_openwebnet_trace_{self.entry_id}",
            )
        else:
            self._task = self.hass.async_create_task(self._async_loop())
        if self._task is not None:
            self._task.add_done_callback(self._handle_task_done)

    async def async_stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def async_restart(self) -> None:
        await self.async_stop()
        await self.async_start()

    async def _async_loop(self) -> None:
        reconnect_delay = 1.0
        while not self._stop.is_set():
            response: ClientResponse | None = None
            try:
                response = await self.client.async_open_openwebnet_trace_stream(
                    last_event_id=self._last_event_id if self._last_event_id > 0 else None,
                )
                reconnect_delay = 1.0
                await self._async_consume(response)
            except asyncio.CancelledError:
                raise
            except CompanionAuthError as err:
                _LOGGER.debug("OpenWebNet trace stream auth error: %s", err)
                await self._async_wait_or_stop(10.0)
            except (CompanionApiError, ClientConnectionError, ClientPayloadError, OSError) as err:
                _LOGGER.debug("OpenWebNet trace stream disconnected: %s", err)
                await self._async_wait_or_stop(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2.0, 30.0)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("OpenWebNet trace stream crashed, retrying: %s", err, exc_info=True)
                await self._async_wait_or_stop(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2.0, 30.0)
            finally:
                if response is not None:
                    response.close()

    async def _async_wait_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            return

    async def _async_consume(self, response: ClientResponse) -> None:
        data_lines: list[str] = []

        while not response.content.at_eof():
            raw_line = await response.content.readline()
            if raw_line == b"":
                break
            line = raw_line.decode(errors="ignore").rstrip("\r\n")
            if line == "":
                self._dispatch(data_lines)
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        if data_lines:
            self._dispatch(data_lines)

    def _dispatch(self, data_lines: list[str]) -> None:
        payload: dict[str, Any] = {}
        if data_lines:
            raw = "\n".join(data_lines)
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    payload = loaded
            except json.JSONDecodeError:
                payload = {}

        event_id = payload.get("id")
        if isinstance(event_id, int) and event_id > 0:
            self._last_event_id = event_id

        event_payload = {"entry_id": self.entry_id, **payload}
        self.hass.bus.async_fire(EVENT_OPENWEBNET_FRAME, event_payload)
        async_dispatcher_send(self.hass, trace_signal(self.entry_id), event_payload)

    def _handle_task_done(self, task: asyncio.Task[None]) -> None:
        """Log unexpected task exits so stream stalls are observable."""
        if task.cancelled():
            return
        err = task.exception()
        if err is None:
            _LOGGER.warning("OpenWebNet trace relay task exited unexpectedly")
            return
        _LOGGER.warning(
            "OpenWebNet trace relay task crashed: %s",
            err,
            exc_info=(type(err), err, err.__traceback__),
        )
