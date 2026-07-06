"""Camera platform for BTicino Companion entrypoint streams."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from webrtc_models import RTCIceCandidateInit

from . import IntegrationRuntime
from .api import CompanionApiError
from .const import CONF_COMPANION_URL, DEFAULT_COMPANION_URL
from .coordinator import CompanionCoordinator
from .device_info import build_device_info
from .entity_registry import reconcile_platform_entities

_DEFAULT_RTSP_PORT = 8554
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: IntegrationRuntime = entry.runtime_data
    coordinator = runtime.coordinator
    known_entrypoint_ids: set[str] = set()

    def _sync_entrypoint_cameras() -> None:
        data = coordinator.data if isinstance(coordinator.data, dict) else {}
        reconcile_platform_entities(
            hass,
            entry,
            platform_domain="camera",
            desired_unique_ids=_desired_camera_unique_ids(entry, data),
            managed_unique_id_prefixes={f"{entry.entry_id}_entrypoint_camera_"},
        )

        entrypoints_container = data.get("entrypoints", {}) if isinstance(data, dict) else {}
        rows = entrypoints_container.get("entrypoints", []) if isinstance(entrypoints_container, dict) else []
        if not isinstance(rows, list):
            return

        new_entities: list[CompanionEntrypointCamera] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("has_stream") is False:
                continue

            entrypoint_id = str(row.get("id", "")).strip()
            if not entrypoint_id or entrypoint_id in known_entrypoint_ids:
                continue

            entrypoint_label = str(row.get("label") or entrypoint_id).strip() or entrypoint_id
            devaddr = str(row.get("devaddr", "")).strip() or None
            known_entrypoint_ids.add(entrypoint_id)
            new_entities.append(
                CompanionEntrypointCamera(
                    entry=entry,
                    coordinator=coordinator,
                    entrypoint_id=entrypoint_id,
                    entrypoint_label=entrypoint_label,
                    devaddr=devaddr,
                )
            )

        if new_entities:
            async_add_entities(new_entities)

    _sync_entrypoint_cameras()
    entry.async_on_unload(coordinator.async_add_listener(_sync_entrypoint_cameras))


def _desired_camera_unique_ids(entry: ConfigEntry, data: dict[str, Any]) -> set[str]:
    desired: set[str] = set()
    entrypoints_container = data.get("entrypoints", {}) if isinstance(data, dict) else {}
    rows = entrypoints_container.get("entrypoints", []) if isinstance(entrypoints_container, dict) else []
    if not isinstance(rows, list):
        return desired
    for row in rows:
        if not isinstance(row, dict) or row.get("has_stream") is False:
            continue
        entrypoint_id = str(row.get("id", "")).strip()
        if entrypoint_id:
            desired.add(f"{entry.entry_id}_entrypoint_camera_{entrypoint_id}")
    return desired


def _entrypoint_supports_stream(coordinator: CompanionCoordinator, entrypoint_id: str) -> bool:
    data = coordinator.data if isinstance(coordinator.data, dict) else {}
    entrypoints_container = data.get("entrypoints", {}) if isinstance(data, dict) else {}
    rows = entrypoints_container.get("entrypoints", []) if isinstance(entrypoints_container, dict) else []
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "")).strip() == entrypoint_id:
            return row.get("has_stream") is not False
    return False


class CompanionEntrypointCamera(CoordinatorEntity[CompanionCoordinator], Camera):
    """Camera entity backed by a specific entrypoint stream."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:video"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        *,
        entry: ConfigEntry,
        coordinator: CompanionCoordinator,
        entrypoint_id: str,
        entrypoint_label: str,
        devaddr: str | None,
    ) -> None:
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._entry = entry
        self._entrypoint_id = entrypoint_id
        self._devaddr = devaddr
        self._client = entry.runtime_data.client
        self._companion_url = str(entry.options.get(CONF_COMPANION_URL, entry.data.get(CONF_COMPANION_URL, DEFAULT_COMPANION_URL))).strip()
        self._attr_name = entrypoint_label
        self._attr_unique_id = f"{entry.entry_id}_entrypoint_camera_{entrypoint_id}"
        self._active_webrtc_sessions: set[str] = set()

    @property
    def device_info(self):
        return build_device_info(self._entry, self.coordinator.data)

    @property
    def available(self) -> bool:
        return self.coordinator.entities_available and _entrypoint_supports_stream(
            self.coordinator,
            self._entrypoint_id,
        )

    @property
    def is_streaming(self) -> bool:
        data = self.coordinator.data if isinstance(self.coordinator.data, dict) else {}
        state = data.get("state", {}) if isinstance(data, dict) else {}
        stream_active = bool((state if isinstance(state, dict) else {}).get("stream_active"))
        active_entrypoint = str((state if isinstance(state, dict) else {}).get("active_entrypoint", "")).strip()
        return stream_active and active_entrypoint == self._entrypoint_id

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "entrypoint_id": self._entrypoint_id,
        }
        if self._devaddr:
            attrs["devaddr"] = self._devaddr
        return attrs

    async def stream_source(self) -> str | None:
        return _build_rtsp_stream_url(self._companion_url, self.coordinator.data, self._entrypoint_id)

    async def async_handle_async_webrtc_offer(
        self,
        offer_sdp: str,
        session_id: str,
        send_message: WebRTCSendMessage,
    ) -> None:
        self._active_webrtc_sessions.add(session_id)
        normalized_offer = _canonicalize_sdp(offer_sdp)
        if not normalized_offer:
            self._active_webrtc_sessions.discard(session_id)
            send_message(WebRTCError("webrtc_offer_failed", "empty offer_sdp"))
            return
        try:
            response = await self._client.async_webrtc_offer(
                entrypoint_id=self._entrypoint_id,
                offer_sdp=normalized_offer,
                session_id=session_id,
            )
        except CompanionApiError as err:
            self._active_webrtc_sessions.discard(session_id)
            send_message(WebRTCError("webrtc_offer_failed", str(err)))
            return

        answer_sdp = _canonicalize_sdp(response.get("answer_sdp", ""))
        if not answer_sdp:
            self._active_webrtc_sessions.discard(session_id)
            send_message(WebRTCError("webrtc_offer_failed", "companion returned empty answer_sdp"))
            return

        send_message(WebRTCAnswer(answer_sdp))
        candidates = response.get("candidates")
        if not isinstance(candidates, list):
            return
        for raw in candidates:
            if not isinstance(raw, dict):
                continue
            candidate = _candidate_from_payload(raw)
            if candidate is None:
                continue
            send_message(WebRTCCandidate(candidate))

    async def async_on_webrtc_candidate(self, session_id: str, candidate: RTCIceCandidateInit) -> None:
        payload = _candidate_to_payload(candidate)
        await self._client.async_webrtc_candidate(session_id=session_id, candidate=payload)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        self._active_webrtc_sessions.discard(session_id)
        self.hass.async_create_task(self._client.async_webrtc_close(session_id=session_id))

    async def async_will_remove_from_hass(self) -> None:
        for session_id in list(self._active_webrtc_sessions):
            self.close_webrtc_session(session_id)
        self._active_webrtc_sessions.clear()
        await super().async_will_remove_from_hass()

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        del width, height
        try:
            return await self._client.async_entrypoint_snapshot_latest(self._entrypoint_id)
        except CompanionApiError as err:
            _LOGGER.debug("Snapshot latest fallback failed for %s: %s", self._entrypoint_id, err)
            return None


def _build_rtsp_stream_url(companion_url: str, data: dict[str, Any] | None, entrypoint_id: str) -> str | None:
    payload = data if isinstance(data, dict) else {}
    state = payload.get("state", {}) if isinstance(payload, dict) else {}
    stream_info = _entrypoint_stream_info(payload, entrypoint_id)
    if stream_info is None:
        return None
    stream_path, stream_port = stream_info

    host = ""
    if companion_url:
        parsed = urlsplit(companion_url)
        host = parsed.hostname or ""

    if not host:
        diagnostics = state.get("diagnostics", {}) if isinstance(state, dict) else {}
        network = diagnostics.get("network", {}) if isinstance(diagnostics, dict) else {}
        host = str((network if isinstance(network, dict) else {}).get("ip", "")).strip()

    if not host:
        return None

    host_for_url = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"rtsp://{host_for_url}:{stream_port}/{stream_path}"


def _entrypoint_stream_info(data: dict[str, Any], entrypoint_id: str) -> tuple[str, int] | None:
    entrypoints_container = data.get("entrypoints", {}) if isinstance(data, dict) else {}
    rows = entrypoints_container.get("entrypoints", []) if isinstance(entrypoints_container, dict) else []
    if not isinstance(rows, list):
        return None

    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("id", "")).strip() != entrypoint_id:
            continue
        stream_path = str(row.get("rtsp_path", "")).strip().lstrip("/")
        if not stream_path:
            return None
        stream_port = _positive_port(row.get("rtsp_port"), _DEFAULT_RTSP_PORT)
        return stream_path, stream_port
    return None


def _positive_port(raw: Any, fallback: int) -> int:
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return fallback
    if port <= 0 or port > 65535:
        return fallback
    return port


def _candidate_to_payload(candidate: RTCIceCandidateInit) -> dict[str, Any]:
    payload = candidate.to_dict()
    out: dict[str, Any] = {"candidate": str(payload.get("candidate", "")).strip()}
    sdp_mid = payload.get("sdpMid")
    if isinstance(sdp_mid, str) and sdp_mid.strip():
        out["sdpMid"] = sdp_mid.strip()
    sdp_mline = payload.get("sdpMLineIndex")
    if isinstance(sdp_mline, int):
        out["sdpMLineIndex"] = sdp_mline
    username_fragment = payload.get("usernameFragment")
    if isinstance(username_fragment, str) and username_fragment.strip():
        out["usernameFragment"] = username_fragment.strip()
    return out


def _candidate_from_payload(raw: dict[str, Any]) -> RTCIceCandidateInit | None:
    candidate = str(raw.get("candidate", "")).strip()
    if not candidate:
        return None
    payload: dict[str, Any] = {"candidate": candidate}
    sdp_mid = raw.get("sdpMid")
    if isinstance(sdp_mid, str) and sdp_mid.strip():
        payload["sdpMid"] = sdp_mid.strip()
    sdp_mline = raw.get("sdpMLineIndex")
    if isinstance(sdp_mline, int):
        payload["sdpMLineIndex"] = sdp_mline
    username_fragment = raw.get("usernameFragment")
    if isinstance(username_fragment, str) and username_fragment.strip():
        payload["usernameFragment"] = username_fragment.strip()
    try:
        return RTCIceCandidateInit.from_dict(payload)
    except Exception:
        return None


def _canonicalize_sdp(raw: Any) -> str:
    text = str(raw or "")
    text = text.strip(" \t\r\n")
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\n", "\r\n") + "\r\n"
