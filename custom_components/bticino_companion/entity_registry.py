"""Entity registry helpers for BTicino Companion."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


def reconcile_platform_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    platform_domain: str,
    desired_unique_ids: Iterable[str],
    managed_unique_ids: Iterable[str] = (),
    managed_unique_id_prefixes: Iterable[str] = (),
) -> None:
    """Remove stale registry entries for dynamic companion entities."""
    desired = {str(unique_id).strip() for unique_id in desired_unique_ids if str(unique_id).strip()}
    managed_exact = {str(unique_id).strip() for unique_id in managed_unique_ids if str(unique_id).strip()}
    managed_prefixes = tuple(
        str(prefix).strip() for prefix in managed_unique_id_prefixes if str(prefix).strip()
    )
    registry = er.async_get(hass)

    for entity_entry in tuple(registry.entities.values()):
        if entity_entry.config_entry_id != entry.entry_id:
            continue
        if entity_entry.platform != DOMAIN:
            continue
        if entity_entry.entity_id.split(".", 1)[0] != platform_domain:
            continue

        unique_id = str(entity_entry.unique_id)
        managed = unique_id in managed_exact or any(
            unique_id.startswith(prefix) for prefix in managed_prefixes
        )
        if managed and unique_id not in desired:
            registry.async_remove(entity_entry.entity_id)
