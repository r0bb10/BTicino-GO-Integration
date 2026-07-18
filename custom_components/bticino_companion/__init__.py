"""BTicino Companion integration setup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import issue_registry as ir

from .api import CompanionApiClient, CompanionAuthError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_COMPANION_URL,
    CONF_VERIFY_SSL,
    DATA_FRONTEND_REGISTERED,
    DOMAIN,
    FRONTEND_PATH,
    ISSUE_CLAIM_RECOVERY,
    PLATFORMS,
)
from .coordinator import CompanionCoordinator
from .dynamic_entities import DynamicEntityManager
from .websocket import CompanionWebSocketError
from .websocket_api import async_register_websocket_commands


@dataclass(slots=True)
class IntegrationRuntime:
    """Objects owned by a loaded config entry."""

    client: CompanionApiClient
    coordinator: CompanionCoordinator
    dynamic_entities: DynamicEntityManager


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Companion config entry."""
    client = CompanionApiClient(
        session=async_get_clientsession(hass),
        base_url=str(entry.data[CONF_COMPANION_URL]),
        access_token=str(entry.data[CONF_ACCESS_TOKEN]),
        verify_ssl=bool(entry.data.get(CONF_VERIFY_SSL, False)),
    )
    coordinator = CompanionCoordinator(hass, client)
    try:
        await coordinator.async_start()
    except CompanionAuthError as err:
        await coordinator.async_stop()
        _ensure_claim_recovery_issue(hass, entry.entry_id)
        raise ConfigEntryAuthFailed(str(err)) from err
    except CompanionWebSocketError as err:
        await coordinator.async_stop()
        raise ConfigEntryNotReady(str(err)) from err

    dynamic_entities = DynamicEntityManager(hass, entry, coordinator, client)
    runtime = IntegrationRuntime(
        client=client,
        coordinator=coordinator,
        dynamic_entities=dynamic_entities,
    )
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await _async_register_frontend(hass)
    dynamic_entities.async_start()
    _delete_claim_recovery_issue(hass, entry.entry_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Expose the bundled Lovelace card and its authenticated HA bridge once."""
    if hass.data.get(DATA_FRONTEND_REGISTERED):
        return

    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_PATH, str(Path(__file__).parent / "www"), cache_headers=False)]
    )
    async_register_websocket_commands(hass)
    hass.data[DATA_FRONTEND_REGISTERED] = True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Companion config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if isinstance(runtime, IntegrationRuntime):
        await runtime.coordinator.async_stop()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


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
    )


def _delete_claim_recovery_issue(hass: HomeAssistant, entry_id: str) -> None:
    ir.async_delete_issue(hass, DOMAIN, _claim_recovery_issue_id(entry_id))
