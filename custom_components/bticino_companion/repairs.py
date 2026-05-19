"""Repair flows for BTicino Companion integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CompanionApiClient, CompanionApiError, CompanionAuthError
from .config_flow import _map_error
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_COMPANION_URL,
    CONF_KEY_ID,
    CONF_REQUEST_TIMEOUT,
    CONF_VERIFY_SSL,
    DEFAULT_COMPANION_URL,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    ISSUE_CLAIM_RECOVERY,
)


class ClaimRecoveryRepairFlow(RepairsFlow):
    """Repair flow to reset and re-claim companion credentials."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._entry_id = entry_id

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm(user_input)

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is None:
                return self.async_abort(reason="entry_missing")
            if not isinstance(entry, ConfigEntry):
                return self.async_abort(reason="entry_missing")

            client = _build_client(self.hass, entry)
            try:
                issued = await client.async_issue_repair_code()
                repair_code = str(issued.get("repair_code", "")).strip()
                if not repair_code:
                    raise CompanionApiError("Companion did not return a repair code")

                reset = await client.async_reset_claim(repair_code)
                claim_code = str(reset.get("claim_code", "")).strip()
                if not claim_code:
                    raise CompanionApiError("Companion did not return a claim code after reset")

                challenge = await client.async_pair_challenge()
                challenge_id = str(challenge.get("challenge_id", "")).strip()
                nonce = str(challenge.get("nonce", "")).strip()
                if not challenge_id or not nonce:
                    raise CompanionApiError("Companion did not return a valid claim challenge")

                claim = await client.async_pair_claim(
                    challenge_id=challenge_id,
                    nonce=nonce,
                    claim_code=claim_code,
                )
                new_token = str(claim.get("access_token", "")).strip()
                new_key_id = str(claim.get("key_id", "")).strip()
                if not new_token:
                    raise CompanionApiError("Companion did not return an access token during re-claim")
            except CompanionAuthError:
                errors["base"] = "invalid_auth"
            except CompanionApiError as err:
                errors["base"] = _map_error(err)
            else:
                updated_data = dict(entry.data)
                updated_data[CONF_ACCESS_TOKEN] = new_token
                updated_data[CONF_KEY_ID] = new_key_id
                self.hass.config_entries.async_update_entry(entry, data=updated_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_create_entry(title="", data={})

        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}), errors=errors)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create a repair flow for a specific issue."""
    if issue_id.startswith(f"{ISSUE_CLAIM_RECOVERY}_"):
        entry_id = issue_id[len(f"{ISSUE_CLAIM_RECOVERY}_") :]
        return ClaimRecoveryRepairFlow(hass, entry_id)
    raise ValueError(f"Unsupported issue id: {issue_id}")


def _build_client(hass: HomeAssistant, entry: ConfigEntry) -> CompanionApiClient:
    companion_url = str(_entry_value(entry, CONF_COMPANION_URL, DEFAULT_COMPANION_URL))
    access_token = str(_entry_value(entry, CONF_ACCESS_TOKEN, ""))
    key_id = str(_entry_value(entry, CONF_KEY_ID, ""))
    verify_ssl = bool(_entry_value(entry, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
    timeout = float(_entry_value(entry, CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT))
    return CompanionApiClient(
        session=async_get_clientsession(hass),
        base_url=companion_url,
        access_token=access_token,
        key_id=key_id,
        verify_ssl=verify_ssl,
        request_timeout=timeout,
    )


def _entry_value(entry: ConfigEntry, key: str, default: Any) -> Any:
    return entry.options.get(key, entry.data.get(key, default))
