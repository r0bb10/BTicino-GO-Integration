"""BTicino Companion integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CompanionApiClient, CompanionApiError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_COMPANION_URL,
    CONF_REQUEST_TIMEOUT,
    CONF_VERIFY_SSL,
    DATA_SERVICES_REGISTERED,
    DEFAULT_ACCESS_TOKEN,
    DEFAULT_COMPANION_URL,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
    SERVICE_CALL_ANSWER,
    SERVICE_CALL_HANGUP,
    SERVICE_ENTRYPOINT_STREAM_START,
    SERVICE_ENTRYPOINT_STREAM_STOP,
    SERVICE_ENTRYPOINT_UNLOCK,
    SERVICE_REFRESH,
)
from .coordinator import CompanionCoordinator


@dataclass(slots=True)
class IntegrationRuntime:
    """Runtime objects kept for each config entry."""

    client: CompanionApiClient
    coordinator: CompanionCoordinator


SERVICE_SCHEMA_ENTRY = vol.Schema({vol.Optional("entry_id"): str})
SERVICE_SCHEMA_ENTRYPOINT = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Required("entrypoint_id"): str,
    }
)


def _entry_value(entry: ConfigEntry, key: str, default: Any) -> Any:
    return entry.options.get(key, entry.data.get(key, default))


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up integration from YAML (unused)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BTicino v2 from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    client = CompanionApiClient(
        session=async_get_clientsession(hass),
        base_url=str(_entry_value(entry, CONF_COMPANION_URL, DEFAULT_COMPANION_URL)),
        access_token=str(_entry_value(entry, CONF_ACCESS_TOKEN, DEFAULT_ACCESS_TOKEN)),
        verify_ssl=bool(_entry_value(entry, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
        request_timeout=float(_entry_value(entry, CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)),
    )
    coordinator = CompanionCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start_event_stream()

    runtime = IntegrationRuntime(client=client, coordinator=coordinator)
    entry.runtime_data = runtime
    hass.data[DOMAIN][entry.entry_id] = runtime

    await _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(runtime, IntegrationRuntime):
        return

    runtime.client.update_runtime_config(
        base_url=str(_entry_value(entry, CONF_COMPANION_URL, DEFAULT_COMPANION_URL)),
        access_token=str(_entry_value(entry, CONF_ACCESS_TOKEN, DEFAULT_ACCESS_TOKEN)),
        verify_ssl=bool(_entry_value(entry, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
        request_timeout=float(_entry_value(entry, CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)),
    )
    await runtime.coordinator.async_request_refresh()
    await runtime.coordinator.async_restart_event_stream()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if isinstance(runtime, IntegrationRuntime):
        await runtime.coordinator.async_stop_event_stream()

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

    async def _handle_entrypoint_unlock(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        entrypoint_id = str(call.data["entrypoint_id"]).strip()
        await _run_command(
            "Entrypoint unlock",
            call,
            lambda: runtime.client.async_entrypoint_unlock(entrypoint_id),
        )

    async def _handle_entrypoint_stream_start(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        entrypoint_id = str(call.data["entrypoint_id"]).strip()
        await _run_command(
            "Entrypoint stream start",
            call,
            lambda: runtime.client.async_entrypoint_stream_start(entrypoint_id),
        )

    async def _handle_entrypoint_stream_stop(call: ServiceCall) -> None:
        runtime = await _resolve_runtime(call)
        entrypoint_id = str(call.data["entrypoint_id"]).strip()
        await _run_command(
            "Entrypoint stream stop",
            call,
            lambda: runtime.client.async_entrypoint_stream_stop(entrypoint_id),
        )

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _handle_refresh, schema=SERVICE_SCHEMA_ENTRY)
    hass.services.async_register(DOMAIN, SERVICE_CALL_ANSWER, _handle_call_answer, schema=SERVICE_SCHEMA_ENTRY)
    hass.services.async_register(DOMAIN, SERVICE_CALL_HANGUP, _handle_call_hangup, schema=SERVICE_SCHEMA_ENTRY)
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENTRYPOINT_UNLOCK,
        _handle_entrypoint_unlock,
        schema=SERVICE_SCHEMA_ENTRYPOINT,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENTRYPOINT_STREAM_START,
        _handle_entrypoint_stream_start,
        schema=SERVICE_SCHEMA_ENTRYPOINT,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_ENTRYPOINT_STREAM_STOP,
        _handle_entrypoint_stream_stop,
        schema=SERVICE_SCHEMA_ENTRYPOINT,
    )

    hass.data[DATA_SERVICES_REGISTERED] = True


async def _async_unregister_services(hass: HomeAssistant) -> None:
    if not hass.data.get(DATA_SERVICES_REGISTERED):
        return

    for service in (
        SERVICE_REFRESH,
        SERVICE_CALL_ANSWER,
        SERVICE_CALL_HANGUP,
        SERVICE_ENTRYPOINT_UNLOCK,
        SERVICE_ENTRYPOINT_STREAM_START,
        SERVICE_ENTRYPOINT_STREAM_STOP,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)

    hass.data.pop(DATA_SERVICES_REGISTERED, None)
