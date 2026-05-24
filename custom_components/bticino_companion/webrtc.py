"""WebRTC session lifecycle manager for BTicino cameras."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

from homeassistant.components.camera import Camera, CameraWebRTCProvider, WebRTCSendMessage
from homeassistant.components.camera.webrtc import async_get_supported_provider
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from webrtc_models import RTCIceCandidateInit

from .api import CompanionApiClient
from .coordinator import CompanionCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _SessionState:
    entrypoint_id: str
    provider: CameraWebRTCProvider


class CompanionWebRTCSessionManager:
    """Owns camera WebRTC sessions and stream start/stop refcounting."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        client: CompanionApiClient,
        coordinator: CompanionCoordinator,
    ) -> None:
        self._hass = hass
        self._client = client
        self._coordinator = coordinator
        self._lock = asyncio.Lock()
        self._sessions: dict[str, _SessionState] = {}
        self._entrypoint_refcounts: dict[str, int] = {}

    async def async_handle_offer(
        self,
        *,
        camera: Camera,
        entrypoint_id: str,
        offer_sdp: str,
        session_id: str,
        send_message: WebRTCSendMessage,
    ) -> None:
        provider = await async_get_supported_provider(self._hass, camera)
        if provider is None:
            raise HomeAssistantError(
                "No WebRTC provider available for camera stream source. "
                "Enable Home Assistant go2rtc/WebRTC support."
            )

        stream_started = False
        async with self._lock:
            if session_id in self._sessions:
                raise HomeAssistantError(f"WebRTC session already exists: {session_id}")

            current = self._entrypoint_refcounts.get(entrypoint_id, 0)
            if current == 0:
                await self._coordinator.async_run_command(
                    label=f"Entrypoint stream start ({entrypoint_id})",
                    command_coro_factory=lambda: self._client.async_entrypoint_stream_start(entrypoint_id),
                )
                stream_started = True

            self._entrypoint_refcounts[entrypoint_id] = current + 1
            self._sessions[session_id] = _SessionState(
                entrypoint_id=entrypoint_id,
                provider=provider,
            )

        try:
            await provider.async_handle_async_webrtc_offer(camera, offer_sdp, session_id, send_message)
        except Exception:
            await self._async_release_session(session_id, close_provider=True)
            raise

        if stream_started:
            _LOGGER.debug("Started entrypoint stream for WebRTC session %s", session_id)

    async def async_on_candidate(self, *, session_id: str, candidate: RTCIceCandidateInit) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise HomeAssistantError(f"Unknown WebRTC session: {session_id}")
        await session.provider.async_on_webrtc_candidate(session_id, candidate)

    def close_session(self, session_id: str) -> None:
        self._hass.async_create_task(self._async_release_session(session_id, close_provider=True))

    async def _async_release_session(self, session_id: str, *, close_provider: bool) -> None:
        session: _SessionState | None
        should_stop = False
        entrypoint_id = ""

        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return

            entrypoint_id = session.entrypoint_id
            current = self._entrypoint_refcounts.get(entrypoint_id, 0)
            if current <= 1:
                self._entrypoint_refcounts.pop(entrypoint_id, None)
                should_stop = True
            else:
                self._entrypoint_refcounts[entrypoint_id] = current - 1

        if close_provider:
            try:
                session.provider.async_close_session(session_id)
            except Exception:
                _LOGGER.debug("Failed to close provider session %s", session_id, exc_info=True)

        if should_stop:
            try:
                await self._coordinator.async_run_command(
                    label=f"Entrypoint stream stop ({entrypoint_id})",
                    command_coro_factory=lambda: self._client.async_entrypoint_stream_stop(entrypoint_id),
                )
            except Exception:
                _LOGGER.warning(
                    "Failed to stop entrypoint stream after closing WebRTC session %s",
                    session_id,
                    exc_info=True,
                )

