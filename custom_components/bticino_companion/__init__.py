"""BTicino Companion integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady, HomeAssistantError
from homeassistant.components.http import StaticPathConfig
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir

from .api import CompanionApiClient, CompanionApiError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_COMPANION_URL,
    CONF_KEY_ID,
    CONF_REQUEST_TIMEOUT,
    CONF_VERIFY_SSL,
    DATA_SERVICES_REGISTERED,
    DEFAULT_ACCESS_TOKEN,
    DEFAULT_COMPANION_URL,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    ISSUE_CLAIM_RECOVERY,
    PLATFORMS,
    SERVICE_CALL_ANSWER,
    SERVICE_CALL_HANGUP,
    SERVICE_AUDIO_MUTE,
    SERVICE_AUDIO_UNMUTE,
    SERVICE_VOICEMAIL_ENABLE,
    SERVICE_VOICEMAIL_DISABLE,
    SERVICE_ENTRYPOINT_UNLOCK,
    SERVICE_SYSTEM_REBOOT,
    SERVICE_REFRESH,
)
from .coordinator import CompanionCoordinator
from .trace_relay import OpenWebNetTraceRelay
from .websocket_api import async_register_websocket_commands


@dataclass(slots=True)
class IntegrationRuntime:
    """Runtime objects kept for each config entry."""

    client: CompanionApiClient
    coordinator: CompanionCoordinator
    trace_relay: OpenWebNetTraceRelay


SERVICE_SCHEMA_ENTRY = vol.Schema({vol.Optional("entry_id"): str})
SERVICE_SCHEMA_ENTRYPOINT = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Required("entrypoint_id"): str,
    }
)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
DATA_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"
FRONTEND_PATH = "/bticino_companion_static"

def _entry_value(entry: ConfigEntry, key: str, default: Any) -> Any:
    return entry.options.get(key, entry.data.get(key, default))


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up integration from YAML (unused)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BTicino from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    async def _async_persist_auth_state(auth_state: dict[str, str]) -> None:
        updates = {
            CONF_ACCESS_TOKEN: auth_state.get("access_token", ""),
            CONF_KEY_ID: auth_state.get("key_id", ""),
        }
        merged = dict(entry.data)
        changed = False
        for key, value in updates.items():
            if value and str(merged.get(key, "")).strip() != value:
                merged[key] = value
                changed = True
        if changed:
            hass.config_entries.async_update_entry(entry, data=merged)

    client = CompanionApiClient(
        session=async_get_clientsession(hass),
        base_url=str(_entry_value(entry, CONF_COMPANION_URL, DEFAULT_COMPANION_URL)),
        access_token=str(_entry_value(entry, CONF_ACCESS_TOKEN, DEFAULT_ACCESS_TOKEN)),
        key_id=str(_entry_value(entry, CONF_KEY_ID, "")),
        verify_ssl=bool(_entry_value(entry, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
        request_timeout=float(_entry_value(entry, CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)),
        auth_state_listener=_async_persist_auth_state,
    )
    coordinator = CompanionCoordinator(hass, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        _ensure_claim_recovery_issue(hass, entry.entry_id)
        raise
    except Exception as err:  # noqa: BLE001
        raise ConfigEntryNotReady(str(err)) from err

    await coordinator.async_start_event_stream()

    trace_relay = OpenWebNetTraceRelay(hass, client, entry.entry_id)
    await trace_relay.async_start()
    runtime = IntegrationRuntime(
        client=client,
        coordinator=coordinator,
        trace_relay=trace_relay,
    )
    entry.runtime_data = runtime
    hass.data[DOMAIN][entry.entry_id] = runtime
    _delete_claim_recovery_issue(hass, entry.entry_id)

    await _async_register_frontend(hass)
    await _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Expose bundled Lovelace card assets and WebSocket commands."""
    if hass.data.get(DATA_FRONTEND_REGISTERED):
        return

    www_path = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_PATH, str(www_path), cache_headers=False)]
    )
    async_register_websocket_commands(hass)
    hass.data[DATA_FRONTEND_REGISTERED] = True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(runtime, IntegrationRuntime):
        return

    runtime.client.update_runtime_config(
        base_url=str(_entry_value(entry, CONF_COMPANION_URL, DEFAULT_COMPANION_URL)),
        access_token=str(_entry_value(entry, CONF_ACCESS_TOKEN, DEFAULT_ACCESS_TOKEN)),
        verify_ssl=bool(_entry_value(entry, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
        request_timeout=float(_entry_value(entry, CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)),
        key_id=str(_entry_value(entry, CONF_KEY_ID, "")),
    )
    await runtime.coordinator.async_request_refresh()
    await runtime.coordinator.async_restart_event_stream()
    await runtime.trace_relay.async_restart()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if isinstance(runtime, IntegrationRuntime):
        _delete_claim_recovery_issue(hass, entry.entry_id)
        await runtime.coordinator.async_stop_event_stream()
        await runtime.trace_relay.async_stop()

    if not hass.data.get(DOMAIN):
        await _async_unregister_services(hass)

    return True


async def _async_register_services(hass: HomeAssistant) -> None:
    if hass.data.get(DATA_SERVICES_REGISTERED):
        return

    async def _resolve_runtime(call: ServiceCall) -> IntegrationRuntime:
        entries = hass.data.get(DOMAIN, {})
        if not isinstance(entries, dict) or not entries:
            raise HomeAssistantError("No BTicino Companion entries are loaded")

        entry_id = call.data.get("entry_id")
        if isinstance(entry_id, str) and entry_id.strip():
            runtime = entries.get(entry_id.strip())
            if not isinstance(runtime, IntegrationRuntime):
                raise HomeAssistantError(f"Entry '{entry_id}' is not loaded")
            return runtime

        first_runtime = next(iter(entries.values()))
        if not isinstance(first_runtime, IntegrationRuntime):
            raise HomeAssistantError("No valid integration runtime loaded")
        return first_runtime

    async def _run_command(label: str, call: ServiceCall, fn) -> None:
        runtime = await _resolve_runtime(call)
        try:
            await runtime.coordinator.async_run_command(label=label, command_coro_factory=fn)
        except CompanionApiError as err:
            raise HomeAssistantError(str(err)) from err

    async def _handle_refresh(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        await runtime.coordinator.async_request_refresh()

    async def _handle_call_answer(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        await _run_command("Call answer", call, runtime.client.async_call_answer)

    async def _handle_call_hangup(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        await _run_command("Call hangup", call, runtime.client.async_call_hangup)

    async def _handle_audio_mute(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        await _run_command("Audio mute", call, runtime.client.async_audio_mute)

    async def _handle_audio_unmute(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        await _run_command("Audio unmute", call, runtime.client.async_audio_unmute)

    async def _handle_voicemail_enable(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        await _run_command("Voicemail enable", call, runtime.client.async_voicemail_enable)

    async def _handle_voicemail_disable(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        await _run_command("Voicemail disable", call, runtime.client.async_voicemail_disable)

    async def _handle_entrypoint_unlock(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        entrypoint_id = str(call.data["entrypoint_id"]).strip()
        await _run_command(
            "Entrypoint unlock",
            call,
            lambda: runtime.client.async_entrypoint_unlock(entrypoint_id),
        )

    async def _handle_system_reboot(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        await _run_command(
            "System reboot",
            call,
            runtime.client.async_system_reboot,
        )

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh, schema=SERVICE_SCHEMA_ENTRY)
    hass.services.async_register(DOMAIN, SERVICE_CALL_ANSWER, _handle_call_answer, schema=SERVICE_SCHEMA_ENTRY)
    hass.services.async_register(DOMAIN, SERVICE_CALL_HANGUP, _handle_call_hangup, schema=SERVICE_SCHEMA_ENTRY)
    hass.services.async_register(DOMAIN, SERVICE_AUDIO_MUTE, _handle_audio_mute, schema=SERVICE_SCHEMA_ENTRY)
    hass.services.async_register(DOMAIN, SERVICE_AUDIO_UNMUTE, _handle_audio_unmute, schema=SERVICE_SCHEMA_ENTRY)
    hass.services.async_register(
        DOMAIN, SERVICE_VOICEMAIL_ENABLE, _handle_voicemail_enable, schema=SERVICE_SCHEMA_ENTRY
    )
    hass.services.async_register(
        DOMAIN, SERVICE_VOICEMAIL_DISABLE, _handle_voicemail_disable, schema=SERVICE_SCHEMA_ENTRY
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENTRYPOINT_UNLOCK,
        _handle_entrypoint_unlock,
        schema=SERVICE_SCHEMA_ENTRYPOINT,
    )
    hass.services.async_register(DOMAIN, SERVICE_SYSTEM_REBOOT, _handle_system_reboot, schema=SERVICE_SCHEMA_ENTRY)

    hass.data[DATA_SERVICES_REGISTERED] = True


async def _async_unregister_services(hass: HomeAssistant) -> None:
    if not hass.data.get(DATA_SERVICES_REGISTERED):
        return

    for service in (
        SERVICE_REFRESH,
        SERVICE_CALL_ANSWER,
        SERVICE_CALL_HANGUP,
        SERVICE_AUDIO_MUTE,
        SERVICE_AUDIO_UNMUTE,
        SERVICE_VOICEMAIL_ENABLE,
        SERVICE_VOICEMAIL_DISABLE,
        SERVICE_ENTRYPOINT_UNLOCK,
        SERVICE_SYSTEM_REBOOT,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)

    hass.data.pop(DATA_SERVICES_REGISTERED, None)


def _claim_recovery_issue_id(entry_id: str) -> str:
    return f"{ISSUE_CLAIM_RECOVERY}_{entry_id}"


def _ensure_claim_recovery_issue(hass: HomeAssistant, entry_id: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        _claim_recovery_issue_id(entry_id),
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_CLAIM_RECOVERY,
        data={"entry_id": entry_id},
    )


def _delete_claim_recovery_issue(hass: HomeAssistant, entry_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, _claim_recovery_issue_id(entry_id))
