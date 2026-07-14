"""Native Home Assistant camera entities for Companion entrypoints."""

from __future__ import annotations

import base64
import logging
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.camera import Camera, CameraEntityFeature, WebRTCAnswer, WebRTCCandidate, WebRTCError, WebRTCSendMessage
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from webrtc_models import RTCIceCandidateInit

from . import IntegrationRuntime
from .api import CompanionApiError
from .coordinator import CompanionCoordinator
from .device_info import device_info
from .models import Entrypoint

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Add one native stream camera for each stream-capable entrypoint."""
    del hass
    runtime: IntegrationRuntime = entry.runtime_data
    known: set[str] = set()

    def _add_cameras() -> None:
        state = runtime.coordinator.data
        if state is None:
            return
        entities = []
        for entrypoint in state.entrypoints:
            unique_id = f"{entry.unique_id}_camera_{entrypoint.id}"
            if entrypoint.capabilities.stream and unique_id not in known:
                known.add(unique_id)
                entities.append(CompanionCamera(entry, runtime.coordinator, runtime.client, entrypoint))
        if entities:
            async_add_entities(entities)

    _add_cameras()
    entry.async_on_unload(runtime.coordinator.async_add_listener(_add_cameras))


class CompanionCamera(CoordinatorEntity[CompanionCoordinator], Camera):
    """A per-entrypoint camera preserving HA's native WebRTC contract."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, entry: ConfigEntry, coordinator: CompanionCoordinator, client: Any, entrypoint: Entrypoint) -> None:
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._entry = entry
        self._client = client
        self._entrypoint = entrypoint
        self._sessions: set[str] = set()
        self._attr_unique_id = f"{entry.unique_id}_camera_{entrypoint.id}"
        self._attr_name = entrypoint.label or entrypoint.id

    @property
    def device_info(self):
        return device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        return self.coordinator.runtime.connected

    async def stream_source(self) -> str | None:
        host = urlsplit(self._client.base_url).hostname
        if not host:
            return None
        host = f"[{host}]" if ":" in host else host
        return f"rtsp://{host}:8554/{self._entrypoint.id}"

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """Request a server-maintained JPEG via the v3 protocol command."""
        del width, height
        try:
            response = await self.coordinator.async_command(
                "entrypoint.snapshot", {"entrypoint_id": self._entrypoint.id}
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unable to request snapshot for %s: %s", self._entrypoint.id, err)
            return None
        if not isinstance(response, dict):
            return None
        data = response.get("data")
        if isinstance(data, str):
            try:
                return base64.b64decode(data)
            except ValueError:
                _LOGGER.debug("Snapshot for %s returned invalid base64 data", self._entrypoint.id)
                return None
        url = response.get("url")
        if isinstance(url, str):
            return await self._async_fetch_image(url)
        return None

    async def _async_fetch_image(self, url: str) -> bytes | None:
        try:
            async with self._client.session.get(url, ssl=self._client.verify_ssl) as response:
                if response.status >= 400:
                    return None
                return await response.read()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unable to fetch snapshot for %s: %s", self._entrypoint.id, err)
            return None

    async def async_handle_async_webrtc_offer(self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage) -> None:
        """Bridge Home Assistant's WebRTC exchange to the v3 API."""
        self._sessions.add(session_id)
        try:
            response = await self._client.async_webrtc_offer(entrypoint_id=self._entrypoint.id, offer_sdp=offer_sdp, session_id=session_id)
        except CompanionApiError as err:
            self._sessions.discard(session_id)
            send_message(WebRTCError("webrtc_offer_failed", str(err)))
            return
        answer_sdp = str(response.get("answer_sdp", "")).strip()
        if not answer_sdp:
            self._sessions.discard(session_id)
            send_message(WebRTCError("webrtc_offer_failed", "Companion returned an empty answer"))
            return
        send_message(WebRTCAnswer(answer_sdp))
        candidates = response.get("candidates", [])
        for raw in candidates if isinstance(candidates, list) else []:
            if isinstance(raw, dict) and str(raw.get("candidate", "")).strip():
                try:
                    send_message(WebRTCCandidate(RTCIceCandidateInit.from_dict(raw)))
                except (TypeError, ValueError):
                    _LOGGER.debug("Ignoring malformed WebRTC candidate from Companion")

    async def async_on_webrtc_candidate(self, session_id: str, candidate: RTCIceCandidateInit) -> None:
        await self._client.async_webrtc_candidate(session_id=session_id, candidate=candidate.to_dict())

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        if session_id in self._sessions:
            self._sessions.remove(session_id)
            self.hass.async_create_task(self._client.async_webrtc_close(session_id=session_id))

    async def async_will_remove_from_hass(self) -> None:
        for session_id in tuple(self._sessions):
            self.close_webrtc_session(session_id)
        await super().async_will_remove_from_hass()
