"""Repair flows for BTicino Companion credentials."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CompanionApiClient, CompanionApiError, CompanionAuthError
from .const import CONF_ACCESS_TOKEN, CONF_COMPANION_URL, CONF_VERIFY_SSL, ISSUE_CLAIM_RECOVERY


class ClaimRecoveryRepairFlow(RepairsFlow):
    """Reset and re-claim Companion credentials."""

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
                str(entry.data[CONF_ACCESS_TOKEN]),
                bool(entry.data.get(CONF_VERIFY_SSL, False)),
            )
            try:
                issued = await client.async_issue_repair_code()
                reset = await client.async_reset_claim(str(issued["repair_code"]))
                challenge = await client.async_pair_challenge()
                claim = await client.async_pair_claim(
                    challenge_id=str(challenge["challenge_id"]), claim_code=str(reset["claim_code"])
                )
            except CompanionAuthError:
                errors["base"] = "invalid_auth"
            except (CompanionApiError, KeyError) as err:
                errors["base"] = err.code if isinstance(err, CompanionApiError) else "cannot_connect"
            else:
                data = {**entry.data, CONF_ACCESS_TOKEN: str(claim["access_token"])}
                self.hass.config_entries.async_update_entry(entry, data=data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}), errors=errors)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    if issue_id.startswith(f"{ISSUE_CLAIM_RECOVERY}_"):
        return ClaimRecoveryRepairFlow(hass, issue_id.removeprefix(f"{ISSUE_CLAIM_RECOVERY}_"))
    raise ValueError(f"Unsupported issue id: {issue_id}")
