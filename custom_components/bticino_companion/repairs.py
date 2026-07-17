"""Repair flows for BTicino Companion credentials."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CompanionApiClient, CompanionApiError, CompanionAuthError
from .const import CONF_ACCESS_TOKEN, CONF_COMPANION_URL, CONF_VERIFY_SSL, ISSUE_CLAIM_RECOVERY

_LOGGER = logging.getLogger(__name__)


class ClaimRecoveryRepairFlow(RepairsFlow):
    """Recover Companion credentials with an owner-issued repair code."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm(user_input)

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> data_entry_flow.FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if not isinstance(entry, ConfigEntry):
                return self.async_abort(reason="entry_missing")
            client = CompanionApiClient(
                async_get_clientsession(self.hass),
                str(entry.data[CONF_COMPANION_URL]),
                "",
                bool(entry.data.get(CONF_VERIFY_SSL, False)),
            )
            try:
                _LOGGER.debug("Starting Companion credential recovery")
                recovered = await client.async_recover_bearer(str(user_input["repair_code"]))
            except CompanionAuthError as err:
                _LOGGER.warning("Companion credential recovery rejected: %s", type(err).__name__)
                errors["base"] = "cannot_connect"
            except (CompanionApiError, KeyError) as err:
                _LOGGER.warning(
                    "Companion credential recovery failed: %s",
                    type(err).__name__,
                )
                errors["base"] = "cannot_connect"
            else:
                data = {**entry.data, CONF_ACCESS_TOKEN: str(recovered["access_token"])}
                self.hass.config_entries.async_update_entry(entry, data=data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                _LOGGER.info("Companion credential recovery completed")
                return self.async_create_entry(title="", data={})
        return self.async_show_form(
            step_id="confirm", data_schema=vol.Schema({vol.Required("repair_code"): str}), errors=errors
        )


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    if issue_id.startswith(f"{ISSUE_CLAIM_RECOVERY}_"):
        return ClaimRecoveryRepairFlow(hass, issue_id.removeprefix(f"{ISSUE_CLAIM_RECOVERY}_"))
    raise ValueError(f"Unsupported issue id: {issue_id}")
