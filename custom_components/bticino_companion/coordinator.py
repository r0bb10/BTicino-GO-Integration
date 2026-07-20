"""Push-only coordinator for Companion state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import CompanionApiClient
from .const import DOMAIN
from .models import CompanionState, RuntimeInfo, TraceFrame
from .websocket import CompanionWebSocket


class CompanionCoordinator(DataUpdateCoordinator[CompanionState]):
    """Own the Companion WebSocket and publish server-pushed state."""

    def __init__(self, hass: HomeAssistant, client: CompanionApiClient, device_id: str) -> None:
        super().__init__(hass, logger=logging.getLogger(__name__), name=DOMAIN)
        self.client = client
        self.last_event: Mapping[str, Any] | None = None
        self.last_trace: TraceFrame | None = None
        self._trace_listeners: list[Callable[[TraceFrame], None]] = []
        self._runtime = RuntimeInfo()
        self.websocket = CompanionWebSocket(
            session=client.session,
            base_url=client.base_url,
            access_token=client.access_token,
            device_id=device_id,
            on_state=self._async_handle_state,
            on_event=self._async_handle_event,
            on_trace=self._async_handle_trace,
            on_connection=self._async_handle_connection,
        )

    @property
    def runtime(self) -> RuntimeInfo:
        """Return current local transport details."""
        return self._runtime

    async def async_start(self) -> None:
        """Start the WebSocket and wait for its initial state."""
        from homeassistant.components import zeroconf

        await self.websocket.async_start(await zeroconf.async_get_instance(self.hass))
        await self.websocket.async_wait_connected()

    async def async_stop(self) -> None:
        """Stop the WebSocket connection."""
        await self.websocket.async_stop()

    async def async_update_base_url(self, base_url: str) -> None:
        """Reconnect the push transport at a newly discovered endpoint."""
        await self.websocket.async_update_base_url(base_url)

    def async_expect_disconnect(self) -> None:
        """Tell the transport that a Companion reboot has been requested."""
        self.websocket.async_expect_disconnect()

    def async_cancel_expected_disconnect(self) -> None:
        """Clear a reboot expectation after a rejected reboot request."""
        self.websocket.async_cancel_expected_disconnect()

    def async_add_trace_listener(self, listener: Callable[[TraceFrame], None]) -> Callable[[], None]:
        """Register a listener for trace frames without creating another transport."""
        self._trace_listeners.append(listener)

        def _remove() -> None:
            self._trace_listeners.remove(listener)

        return _remove

    async def _async_update_data(self) -> CompanionState:
        if self.data is None:
            await self.websocket.async_wait_connected()
        return self.data

    async def _async_handle_state(self, state: CompanionState) -> None:
        self.async_set_updated_data(state)

    async def _async_handle_event(self, event: Mapping[str, Any]) -> None:
        self.last_event = event

    async def _async_handle_trace(self, trace: TraceFrame) -> None:
        self.last_trace = trace
        for listener in tuple(self._trace_listeners):
            listener(trace)

    async def _async_handle_connection(
        self, connected: bool, last_error: str | None, reconnect_attempts: int
    ) -> None:
        self._runtime = RuntimeInfo(
            connected=connected,
            last_error=last_error,
            reconnect_attempts=reconnect_attempts,
        )
        if self.data is not None:
            self.async_update_listeners()
