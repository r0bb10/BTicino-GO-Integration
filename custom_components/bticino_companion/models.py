"""Typed Companion API and WebSocket payload models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def mapping_at(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Return a nested mapping or an empty mapping."""
    value = payload.get(key)
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True, slots=True)
class Capability:
    """Features configured for an entrypoint."""

    stream: bool = False
    unlock: bool = False
    ring: bool = False

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Capability":
        return cls(
            stream=bool(payload.get("stream", False)),
            unlock=bool(payload.get("unlock", False)),
            ring=bool(payload.get("ring", False)),
        )


@dataclass(frozen=True, slots=True)
class Entrypoint:
    """A configured intercom entrypoint."""

    id: str
    label: str
    devaddr: str
    capabilities: Capability

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Entrypoint":
        return cls(
            id=str(payload.get("id", "")).strip(),
            label=str(payload.get("label", "")).strip(),
            devaddr=str(payload.get("devaddr", "")).strip(),
            capabilities=Capability.from_dict(mapping_at(payload, "capabilities")),
        )


@dataclass(frozen=True, slots=True)
class RuntimeInfo:
    """Local integration transport status."""

    connected: bool = False
    reconnect_attempts: int = 0
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class TraceFrame:
    """An OpenWebNet frame multiplexed over the Companion WebSocket."""

    direction: str
    frame: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TraceFrame":
        return cls(
            direction=str(payload.get("direction", "")).strip(),
            frame=str(payload.get("frame", "")).strip(),
        )


@dataclass(frozen=True, slots=True)
class CompanionState:
    """Current Companion state projected by the server."""

    revision: int = 0
    call_state: str = "idle"
    active_entrypoint_id: str | None = None
    incoming_dialog_id: str | None = None
    active_dialog_id: str | None = None
    preview_stream_id: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CompanionState":
        physical_ring = mapping_at(payload, "physical_ring")
        incoming_call = mapping_at(payload, "incoming_call")
        active_call = mapping_at(payload, "active_call")
        preview_stream = mapping_at(payload, "preview_stream")
        entrypoint_id = next(
            (
                str(source.get("entrypoint_id", "")).strip()
                for source in (active_call, incoming_call, physical_ring, preview_stream)
                if str(source.get("entrypoint_id", "")).strip()
            ),
            None,
        )
        revision = payload.get("revision", 0)
        return cls(
            revision=revision if isinstance(revision, int) else 0,
            call_state=str(payload.get("call_state", "idle")).strip().lower() or "idle",
            active_entrypoint_id=entrypoint_id,
            incoming_dialog_id=_optional_string(incoming_call.get("dialog_id")),
            active_dialog_id=_optional_string(active_call.get("dialog_id")),
            preview_stream_id=_optional_string(preview_stream.get("stream_id")),
        )


def _optional_string(value: Any) -> str | None:
    value = str(value).strip() if value is not None else ""
    return value or None
