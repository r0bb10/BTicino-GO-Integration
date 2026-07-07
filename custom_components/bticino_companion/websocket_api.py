"""WebSocket commands for BTicino Companion frontend cards."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .api import CompanionApiError
from .const import DOMAIN


def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register WebSocket commands used by the Lovelace card."""
    websocket_api.async_register_command(hass, _handle_webrtc_offer)
    websocket_api.async_register_command(hass, _handle_webrtc_candidate)
    websocket_api.async_register_command(hass, _handle_webrtc_close)


def _runtime_for_message(hass: HomeAssistant, msg: dict[str, Any]) -> Any:
    entries = hass.data.get(DOMAIN, {})
    if not isinstance(entries, dict) or not entries:
        raise HomeAssistantError("No BTicino Companion entries are loaded")

    entry_id = str(msg.get("entry_id", "")).strip()
    if entry_id:
        runtime = entries.get(entry_id)
        if runtime is not None:
            return runtime
        raise HomeAssistantError(f"Entry '{entry_id}' is not loaded")

    runtime = next(iter(entries.values()))
    if runtime is None:
        raise HomeAssistantError("No valid BTicino Companion runtime loaded")
    return runtime


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "bticino_companion/webrtc_offer",
        vol.Optional("entry_id"): str,
        vol.Required("entrypoint_id"): str,
        vol.Required("session_id"): str,
        vol.Required("offer_sdp"): str,
    }
)
@websocket_api.async_response
async def _handle_webrtc_offer(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Forward a frontend WebRTC offer to Companion."""
    try:
        runtime = _runtime_for_message(hass, msg)
        response = await runtime.client.async_webrtc_offer(
            entrypoint_id=str(msg["entrypoint_id"]).strip(),
            session_id=str(msg["session_id"]).strip(),
            offer_sdp=str(msg["offer_sdp"]),
        )
    except (CompanionApiError, HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "webrtc_offer_failed", str(err))
        return

    connection.send_result(msg["id"], response)


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "bticino_companion/webrtc_candidate",
        vol.Optional("entry_id"): str,
        vol.Required("session_id"): str,
        vol.Required("candidate"): dict,
    }
)
@websocket_api.async_response
async def _handle_webrtc_candidate(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Forward a frontend WebRTC candidate to Companion."""
    try:
        runtime = _runtime_for_message(hass, msg)
        response = await runtime.client.async_webrtc_candidate(
            session_id=str(msg["session_id"]).strip(),
            candidate=msg["candidate"],
        )
    except (CompanionApiError, HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "webrtc_candidate_failed", str(err))
        return

    connection.send_result(msg["id"], response)


@callback
@websocket_api.websocket_command(
    {
        vol.Required("type"): "bticino_companion/webrtc_close",
        vol.Optional("entry_id"): str,
        vol.Required("session_id"): str,
    }
)
@websocket_api.async_response
async def _handle_webrtc_close(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Close a Companion WebRTC session."""
    try:
        runtime = _runtime_for_message(hass, msg)
        response = await runtime.client.async_webrtc_close(session_id=str(msg["session_id"]).strip())
    except (CompanionApiError, HomeAssistantError, ValueError) as err:
        connection.send_error(msg["id"], "webrtc_close_failed", str(err))
        return

    connection.send_result(msg["id"], response)
