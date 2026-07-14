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
class SystemService:
    """A Companion service optionally exposed to Home Assistant."""

    name: str
    enabled: bool = False
    exposed: bool = False


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    """Companion update state supplied by the v3 server."""

    enabled: bool = False
    exposed: bool = False
    installed_version: str | None = None
    latest_version: str | None = None
    in_progress: bool = False


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
    entrypoints: tuple[Entrypoint, ...] = ()
    muted: bool = False
    voicemail_enabled: bool | None = None
    reboot_enabled: bool = False
    services: tuple[SystemService, ...] = ()
    update: UpdateInfo = UpdateInfo()
    model: str | None = None
    firmware: str | None = None
    hardware: str | None = None

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
        audio = mapping_at(payload, "audio")
        voicemail = mapping_at(payload, "voicemail")
        system = mapping_at(payload, "system")
        system_control = mapping_at(payload, "system_control")
        device = mapping_at(payload, "device")
        update_payload = mapping_at(system_control, "update") or mapping_at(system, "update")
        services_payload = mapping_at(system_control, "services") or mapping_at(system, "services")
        entrypoints = _entrypoints(payload)
        return cls(
            revision=revision if isinstance(revision, int) else 0,
            call_state=str(payload.get("call_state", "idle")).strip().lower() or "idle",
            active_entrypoint_id=entrypoint_id,
            incoming_dialog_id=_optional_string(incoming_call.get("dialog_id")),
            active_dialog_id=_optional_string(active_call.get("dialog_id")),
            preview_stream_id=_optional_string(preview_stream.get("stream_id")),
            entrypoints=entrypoints,
            muted=bool(audio.get("muted", payload.get("muted", False))),
            voicemail_enabled=_optional_bool(voicemail.get("enabled", payload.get("voicemail_enabled"))),
            reboot_enabled=bool(system_control.get("reboot_enabled", system.get("reboot_enabled", False))),
            services=_services(services_payload),
            update=UpdateInfo(
                enabled=bool(update_payload.get("enabled", system.get("update_enabled", False))),
                exposed=bool(update_payload.get("exposed", system.get("update_exposed", False))),
                installed_version=_optional_string(update_payload.get("current_version", device.get("firmware"))),
                latest_version=_optional_string(mapping_at(update_payload, "available").get("version", update_payload.get("latest_version"))),
                in_progress=bool(update_payload.get("in_progress", False)),
            ),
            model=_optional_string(device.get("model", payload.get("model"))),
            firmware=_optional_string(device.get("firmware", payload.get("firmware"))),
            hardware=_optional_string(device.get("hardware", payload.get("hardware"))),
        )


def _optional_string(value: Any) -> str | None:
    value = str(value).strip() if value is not None else ""
    return value or None


def _optional_bool(value: Any) -> bool | None:
    """Return a boolean only when the server supplied one."""
    return value if isinstance(value, bool) else None


def _entrypoints(payload: Mapping[str, Any]) -> tuple[Entrypoint, ...]:
    raw = payload.get("entrypoints")
    if isinstance(raw, Mapping):
        raw = raw.get("entrypoints")
    if not isinstance(raw, list):
        return ()
    return tuple(entrypoint for item in raw if isinstance(item, Mapping) and (entrypoint := Entrypoint.from_dict(item)).id)


def _services(payload: Mapping[str, Any]) -> tuple[SystemService, ...]:
    return tuple(
        SystemService(name=str(name).strip(), enabled=bool(value.get("enabled")), exposed=bool(value.get("exposed")))
        for name, value in payload.items()
        if isinstance(value, Mapping) and str(name).strip()
    )
