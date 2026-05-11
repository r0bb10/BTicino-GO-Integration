"""Config flow for BTicino Companion integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import CompanionApiClient, CompanionApiError, CompanionAuthError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLAIM_CODE,
    CONF_COMPANION_URL,
    CONF_KEY_ID,
    CONF_REQUEST_TIMEOUT,
    CONF_VERIFY_SSL,
    DEFAULT_ACCESS_TOKEN,
    DEFAULT_COMPANION_URL,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    NAME,
)


def _normalize_url(raw: str) -> str:
    candidate = raw.strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    return candidate.rstrip("/")


def _entry_value(entry: ConfigEntry, key: str, default: Any) -> Any:
    return entry.options.get(key, entry.data.get(key, default))


class CompanionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle BTicino config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            companion_url = _normalize_url(str(user_input.get(CONF_COMPANION_URL, "")))
            access_token = str(user_input.get(CONF_ACCESS_TOKEN, DEFAULT_ACCESS_TOKEN)).strip()
            claim_code = str(user_input.get(CONF_CLAIM_CODE, "")).strip()
            verify_ssl = bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
            timeout = float(user_input.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT))

            try:
                result = await self._async_validate_or_claim(
                    companion_url=companion_url,
                    access_token=access_token,
                    claim_code=claim_code,
                    verify_ssl=verify_ssl,
                    timeout=timeout,
                )
            except CompanionAuthError:
                errors["base"] = "invalid_auth"
            except CompanionApiError as err:
                errors["base"] = _map_error(err)
            else:
                await self.async_set_unique_id(result["unique_id"])
                self._abort_if_unique_id_configured(updates={CONF_COMPANION_URL: companion_url})
                return self.async_create_entry(
                    title=f"{NAME} ({result['title_host']})",
                    data={
                        CONF_COMPANION_URL: companion_url,
                        CONF_ACCESS_TOKEN: result[CONF_ACCESS_TOKEN],
                        CONF_KEY_ID: result.get(CONF_KEY_ID, ""),
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_REQUEST_TIMEOUT: timeout,
                    },
                )

        return self.async_show_form(step_id="user", data_schema=_user_schema(user_input), errors=errors)

    @staticmethod
    def async_get_options_flow(entry: ConfigEntry) -> "CompanionOptionsFlow":
        return CompanionOptionsFlow(entry)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Perform reauthentication when companion rejects stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm and execute credential refresh."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        companion_url = str(_entry_value(reauth_entry, CONF_COMPANION_URL, DEFAULT_COMPANION_URL))
        verify_ssl = bool(_entry_value(reauth_entry, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
        timeout = float(_entry_value(reauth_entry, CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT))

        if user_input is not None:
            access_token = str(user_input.get(CONF_ACCESS_TOKEN, "")).strip()
            claim_code = str(user_input.get(CONF_CLAIM_CODE, "")).strip()

            try:
                result = await self._async_validate_or_claim(
                    companion_url=companion_url,
                    access_token=access_token,
                    claim_code=claim_code,
                    verify_ssl=verify_ssl,
                    timeout=timeout,
                )
            except CompanionAuthError:
                errors["base"] = "invalid_auth"
            except CompanionApiError as err:
                errors["base"] = _map_error(err)
            else:
                await self.async_set_unique_id(result["unique_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={
                        CONF_ACCESS_TOKEN: result[CONF_ACCESS_TOKEN],
                        CONF_KEY_ID: result.get(CONF_KEY_ID, ""),
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(),
            errors=errors,
        )

    async def _async_validate_or_claim(
        self,
        *,
        companion_url: str,
        access_token: str,
        claim_code: str,
        verify_ssl: bool,
        timeout: float,
    ) -> dict[str, str]:
        if not companion_url:
            raise CompanionApiError("companion_url_required", code="companion_url_required")

        client = CompanionApiClient(
            session=async_get_clientsession(self.hass),
            base_url=companion_url,
            access_token=access_token,
            verify_ssl=verify_ssl,
            request_timeout=timeout,
        )

        await client.async_get_health()

        auth_status: dict[str, Any]
        if access_token:
            auth_status = await client.async_get_auth_status(auth=True)
        else:
            try:
                auth_status = await client.async_get_auth_status(auth=False)
            except CompanionAuthError as err:
                raise CompanionApiError("token_required", code="token_required", status=401) from err

        needs_claim = bool(auth_status.get("needs_claim"))
        token_to_store = access_token

        if needs_claim:
            if not claim_code:
                raise CompanionApiError("claim_code_required", code="claim_code_required", status=400)

            challenge = await client.async_pair_challenge()
            challenge_id = str(challenge.get("challenge_id", "")).strip()
            nonce = str(challenge.get("nonce", "")).strip()
            if not challenge_id or not nonce:
                raise CompanionApiError("invalid_challenge_response", code="invalid_challenge_response")

            claim = await client.async_pair_claim(
                challenge_id=challenge_id,
                nonce=nonce,
                claim_code=claim_code,
            )
            token_to_store = str(claim.get("access_token", "")).strip()
            if not token_to_store:
                raise CompanionApiError("missing_access_token", code="missing_access_token")
        else:
            if not token_to_store:
                raise CompanionApiError("token_required", code="token_required", status=401)

        client.update_runtime_config(
            base_url=companion_url,
            access_token=token_to_store,
            verify_ssl=verify_ssl,
            request_timeout=timeout,
        )
        await client.async_get_state()

        parsed = urlsplit(companion_url)
        host = parsed.hostname or companion_url
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        unique_id = f"{DOMAIN}_{host}_{port}"

        return {
            CONF_ACCESS_TOKEN: token_to_store,
            CONF_KEY_ID: str(claim.get("key_id", "")).strip() if needs_claim else "",
            "unique_id": unique_id,
            "title_host": f"{host}:{port}",
        }


class CompanionOptionsFlow(OptionsFlow):
    """Manage BTicino options."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: Mapping[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        current_url = _normalize_url(str(_entry_value(self._entry, CONF_COMPANION_URL, DEFAULT_COMPANION_URL)))
        current_token = str(_entry_value(self._entry, CONF_ACCESS_TOKEN, DEFAULT_ACCESS_TOKEN))
        current_verify_ssl = bool(_entry_value(self._entry, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
        current_timeout = float(_entry_value(self._entry, CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT))

        if user_input is not None:
            companion_url = _normalize_url(str(user_input.get(CONF_COMPANION_URL, "")))
            access_token = str(user_input.get(CONF_ACCESS_TOKEN, "")).strip()
            verify_ssl = bool(user_input.get(CONF_VERIFY_SSL, current_verify_ssl))
            timeout = float(user_input.get(CONF_REQUEST_TIMEOUT, current_timeout))

            client = CompanionApiClient(
                session=async_get_clientsession(self.hass),
                base_url=companion_url,
                access_token=access_token,
                verify_ssl=verify_ssl,
                request_timeout=timeout,
            )
            try:
                await client.async_get_health()
                await client.async_get_state()
            except CompanionAuthError:
                errors["base"] = "invalid_auth"
            except CompanionApiError as err:
                errors["base"] = _map_error(err)
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_COMPANION_URL: companion_url,
                        CONF_ACCESS_TOKEN: access_token,
                        CONF_KEY_ID: str(_entry_value(self._entry, CONF_KEY_ID, "")),
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_REQUEST_TIMEOUT: timeout,
                    },
                )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COMPANION_URL, default=current_url): str,
                    vol.Required(CONF_ACCESS_TOKEN, default=current_token): str,
                    vol.Required(CONF_VERIFY_SSL, default=current_verify_ssl): bool,
                    vol.Required(CONF_REQUEST_TIMEOUT, default=current_timeout): vol.Coerce(float),
                }
            ),
            errors=errors,
        )


def _user_schema(user_input: Mapping[str, Any] | None = None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_COMPANION_URL,
                default=str(user_input.get(CONF_COMPANION_URL, DEFAULT_COMPANION_URL)),
            ): str,
            vol.Optional(
                CONF_ACCESS_TOKEN,
                default=str(user_input.get(CONF_ACCESS_TOKEN, DEFAULT_ACCESS_TOKEN)),
            ): str,
            vol.Optional(
                CONF_CLAIM_CODE,
                default=str(user_input.get(CONF_CLAIM_CODE, "")),
            ): str,
            vol.Required(
                CONF_VERIFY_SSL,
                default=bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
            ): bool,
            vol.Required(
                CONF_REQUEST_TIMEOUT,
                default=float(user_input.get(CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT)),
            ): vol.Coerce(float),
        }
    )


def _reauth_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_ACCESS_TOKEN, default=""): str,
            vol.Optional(CONF_CLAIM_CODE, default=""): str,
        }
    )


def _map_error(err: CompanionApiError) -> str:
    code = (err.code or "").strip().lower()

    if code in {
        "token_required",
        "unauthorized",
        "invalid_auth",
    }:
        return "invalid_auth"
    if code in {
        "claim_code_required",
        "invalid_claim_code",
    }:
        return "invalid_claim_code"
    if code in {
        "invalid_challenge",
        "invalid_challenge_response",
    }:
        return "invalid_challenge"
    if code == "already_claimed":
        return "already_claimed"
    if code in {
        "rate_limit_ip",
        "rate_limit_global",
        "ip_locked",
        "global_locked",
    }:
        return "pairing_locked"
    if code in {
        "invalid_repair_code",
        "repair_code_expired",
    }:
        return "repair_code_invalid"
    if code == "repair_not_allowed":
        return "repair_not_allowed"
    if code == "companion_url_required":
        return "invalid_url"
    return "cannot_connect"
