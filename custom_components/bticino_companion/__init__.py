"""BTicino Companion integration setup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CompanionApiClient, CompanionAuthError
from .const import CONF_ACCESS_TOKEN, CONF_COMPANION_URL, CONF_VERIFY_SSL, DOMAIN
from .coordinator import CompanionCoordinator
from .websocket import CompanionWebSocketError


@dataclass(slots=True)
class IntegrationRuntime:
    """Objects owned by a loaded config entry."""

    client: CompanionApiClient
    coordinator: CompanionCoordinator


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
        raise ConfigEntryAuthFailed(str(err)) from err
    except CompanionWebSocketError as err:
        await coordinator.async_stop()
        raise ConfigEntryNotReady(str(err)) from err

    runtime = IntegrationRuntime(client=client, coordinator=coordinator)
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Companion config entry."""
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if isinstance(runtime, IntegrationRuntime):
        await runtime.coordinator.async_stop()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
