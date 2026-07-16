"""Pure WebSocket wire-protocol helpers."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any


class ProtocolError(ValueError):
    """Raised when a wire message is malformed."""


def ping_message(ping_id: str) -> dict[str, str]:
    """Create a protocol ping message."""
    return {"type": "ping", "id": ping_id}


def parse_message(message: str) -> Mapping[str, Any]:
    """Decode and validate a Companion WebSocket message envelope."""
    try:
        payload = json.loads(message)
    except json.JSONDecodeError as err:
        raise ProtocolError("Companion sent invalid WebSocket JSON") from err
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        raise ProtocolError("Companion sent an invalid WebSocket envelope")
    return payload
