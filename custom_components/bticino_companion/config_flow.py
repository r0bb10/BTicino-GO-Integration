"""Config flow for BTicino Companion integration."""

from __future__ import annotations

from collections.abc import Mapping
import ipaddress
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # pragma: no cover - compatibility with older cores
    from homeassistant.components.zeroconf import ZeroconfServiceInfo  # type: ignore

from .api import CompanionApiClient, CompanionApiError, CompanionAuthError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_TOKEN_EXPIRES_AT,
    CONF_CLAIM_CODE,
    CONF_COMPANION_URL,
    CONF_KEY_ID,
    CONF_REFRESH_TOKEN,
    CONF_REFRESH_TOKEN_EXPIRES_AT,
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

    def __init__(self) -> None:
        self._discovered_url: str | None = None
        self._discovered_device_name: str | None = None

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
                    refresh_token="",
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
                        CONF_REFRESH_TOKEN: result.get(CONF_REFRESH_TOKEN, ""),
                        CONF_ACCESS_TOKEN_EXPIRES_AT: result.get(CONF_ACCESS_TOKEN_EXPIRES_AT, ""),
                        CONF_REFRESH_TOKEN_EXPIRES_AT: result.get(CONF_REFRESH_TOKEN_EXPIRES_AT, ""),
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_REQUEST_TIMEOUT: timeout,
                    },
                )

        return self.async_show_form(step_id="user", data_schema=_user_schema(user_input), errors=errors)

    @staticmethod
    def async_get_options_flow(entry: ConfigEntry) -> "CompanionOptionsFlow":
        return CompanionOptionsFlow(entry)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle discovery via companion mDNS advertisement."""
        companion_url = _companion_url_from_discovery(discovery_info)
        if companion_url is None:
            return self.async_abort(reason="cannot_connect")

        parsed = urlsplit(companion_url)
        host = parsed.hostname or ""
        if host:
            unique_id = f"{DOMAIN}_{host}_{parsed.port or 80}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured(updates={CONF_COMPANION_URL: companion_url})

        client = CompanionApiClient(
            session=async_get_clientsession(self.hass),
            base_url=companion_url,
            access_token="",
            verify_ssl=DEFAULT_VERIFY_SSL,
            request_timeout=DEFAULT_REQUEST_TIMEOUT,
        )
        try:
            await client.async_get_health()
        except CompanionApiError:
            return self.async_abort(reason="cannot_connect")

        self._discovered_url = companion_url
        self._discovered_device_name = _txt_property(discovery_info.properties, "name") or discovery_info.name
        self.context["title_placeholders"] = {"name": self._discovered_device_name or NAME}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Finalize discovered device onboarding."""
        errors: dict[str, str] = {}

        if not self._discovered_url:
            return self.async_abort(reason="cannot_connect")

        if user_input is not None:
            access_token = str(user_input.get(CONF_ACCESS_TOKEN, "")).strip()
            claim_code = str(user_input.get(CONF_CLAIM_CODE, "")).strip()

            try:
                result = await self._async_validate_or_claim(
                    companion_url=self._discovered_url,
                    access_token=access_token,
                    refresh_token="",
                    claim_code=claim_code,
                    verify_ssl=DEFAULT_VERIFY_SSL,
                    timeout=DEFAULT_REQUEST_TIMEOUT,
                )
            except CompanionAuthError:
                errors["base"] = "invalid_auth"
            except CompanionApiError as err:
                errors["base"] = _map_error(err)
            else:
                await self.async_set_unique_id(result["unique_id"])
                self._abort_if_unique_id_configured(updates={CONF_COMPANION_URL: self._discovered_url})
                return self.async_create_entry(
                    title=f"{NAME} ({result['title_host']})",
                    data={
                        CONF_COMPANION_URL: self._discovered_url,
                        CONF_ACCESS_TOKEN: result[CONF_ACCESS_TOKEN],
                        CONF_KEY_ID: result.get(CONF_KEY_ID, ""),
                        CONF_REFRESH_TOKEN: result.get(CONF_REFRESH_TOKEN, ""),
                        CONF_ACCESS_TOKEN_EXPIRES_AT: result.get(CONF_ACCESS_TOKEN_EXPIRES_AT, ""),
                        CONF_REFRESH_TOKEN_EXPIRES_AT: result.get(CONF_REFRESH_TOKEN_EXPIRES_AT, ""),
                        CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
                        CONF_REQUEST_TIMEOUT: DEFAULT_REQUEST_TIMEOUT,
                    },
                )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=_zeroconf_confirm_schema(),
            description_placeholders={
                "url": self._discovered_url,
                "name": self._discovered_device_name or "BTicino Companion",
            },
            errors=errors,
        )

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
        refresh_token = str(_entry_value(reauth_entry, CONF_REFRESH_TOKEN, "")).strip()

        if user_input is not None:
            access_token = str(user_input.get(CONF_ACCESS_TOKEN, "")).strip()
            claim_code = str(user_input.get(CONF_CLAIM_CODE, "")).strip()

            try:
                result = await self._async_validate_or_claim(
                    companion_url=companion_url,
                    access_token=access_token,
                    refresh_token=refresh_token,
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
                        CONF_REFRESH_TOKEN: result.get(CONF_REFRESH_TOKEN, ""),
                        CONF_ACCESS_TOKEN_EXPIRES_AT: result.get(CONF_ACCESS_TOKEN_EXPIRES_AT, ""),
                        CONF_REFRESH_TOKEN_EXPIRES_AT: result.get(CONF_REFRESH_TOKEN_EXPIRES_AT, ""),
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of an existing companion entry."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        companion_url_default = str(_entry_value(reconfigure_entry, CONF_COMPANION_URL, DEFAULT_COMPANION_URL))
        verify_ssl_default = bool(_entry_value(reconfigure_entry, CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
        timeout_default = float(_entry_value(reconfigure_entry, CONF_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT))
        refresh_token_default = str(_entry_value(reconfigure_entry, CONF_REFRESH_TOKEN, "")).strip()

        if user_input is not None:
            companion_url = _normalize_url(str(user_input.get(CONF_COMPANION_URL, "")))
            access_token = str(user_input.get(CONF_ACCESS_TOKEN, "")).strip()
            claim_code = str(user_input.get(CONF_CLAIM_CODE, "")).strip()
            verify_ssl = bool(user_input.get(CONF_VERIFY_SSL, verify_ssl_default))
            timeout = float(user_input.get(CONF_REQUEST_TIMEOUT, timeout_default))

            try:
                result = await self._async_validate_or_claim(
                    companion_url=companion_url,
                    access_token=access_token,
                    refresh_token=refresh_token_default,
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
                    reconfigure_entry,
                    title=f"{NAME} ({result['title_host']})",
                    data_updates={
                        CONF_COMPANION_URL: companion_url,
                        CONF_ACCESS_TOKEN: result[CONF_ACCESS_TOKEN],
                        CONF_KEY_ID: result.get(CONF_KEY_ID, ""),
                        CONF_REFRESH_TOKEN: result.get(CONF_REFRESH_TOKEN, ""),
                        CONF_ACCESS_TOKEN_EXPIRES_AT: result.get(CONF_ACCESS_TOKEN_EXPIRES_AT, ""),
                        CONF_REFRESH_TOKEN_EXPIRES_AT: result.get(CONF_REFRESH_TOKEN_EXPIRES_AT, ""),
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_REQUEST_TIMEOUT: timeout,
                    },
                    options={
                        **reconfigure_entry.options,
                        CONF_COMPANION_URL: companion_url,
                        CONF_VERIFY_SSL: verify_ssl,
                        CONF_REQUEST_TIMEOUT: timeout,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_reconfigure_schema(
                companion_url=companion_url_default,
                verify_ssl=verify_ssl_default,
                timeout=timeout_default,
                user_input=user_input,
            ),
            errors=errors,
        )

    async def _async_validate_or_claim(
        self,
        *,
        companion_url: str,
        access_token: str,
        refresh_token: str,
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
            refresh_token=refresh_token,
            verify_ssl=verify_ssl,
            request_timeout=timeout,
        )

        await client.async_get_health()
        auth_details = await client.async_get_auth_status(auth=bool(access_token or refresh_token))

        claim: dict[str, Any] = {}
        token_to_store = _auth_value(auth_details, "access_token") or access_token
        needs_claim = _auth_bool(auth_details, "needs_claim")

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

        if not token_to_store:
            raise CompanionApiError("token_required", code="token_required", status=401)

        client.update_runtime_config(
            base_url=companion_url,
            access_token=token_to_store,
            verify_ssl=verify_ssl,
            request_timeout=timeout,
        )
        await client.async_get_state()
        auth_details = await client.async_get_auth_status(auth=True)

        parsed = urlsplit(companion_url)
        host = parsed.hostname or companion_url
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        unique_id = f"{DOMAIN}_{host}_{port}"

        final_access = _auth_value(auth_details, "access_token") or token_to_store
        key_id = _auth_value(auth_details, "key_id") or _auth_value(claim, "key_id")
        refresh = _auth_value(auth_details, "refresh_token") or _auth_value(claim, "refresh_token")
        access_expires = _auth_value(auth_details, "access_token_expires_at") or _auth_value(
            claim, "access_token_expires_at"
        )
        refresh_expires = _auth_value(auth_details, "refresh_token_expires_at") or _auth_value(
            claim, "refresh_token_expires_at"
        )

        return {
            CONF_ACCESS_TOKEN: final_access,
            CONF_KEY_ID: key_id,
            CONF_REFRESH_TOKEN: refresh,
            CONF_ACCESS_TOKEN_EXPIRES_AT: access_expires,
            CONF_REFRESH_TOKEN_EXPIRES_AT: refresh_expires,
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

        current_key_id = str(_entry_value(self._entry, CONF_KEY_ID, "")).strip()
        current_refresh = str(_entry_value(self._entry, CONF_REFRESH_TOKEN, "")).strip()
        current_access_expires = str(_entry_value(self._entry, CONF_ACCESS_TOKEN_EXPIRES_AT, "")).strip()
        current_refresh_expires = str(_entry_value(self._entry, CONF_REFRESH_TOKEN_EXPIRES_AT, "")).strip()

        if user_input is not None:
            companion_url = _normalize_url(str(user_input.get(CONF_COMPANION_URL, "")))
            access_token = str(user_input.get(CONF_ACCESS_TOKEN, "")).strip()
            verify_ssl = bool(user_input.get(CONF_VERIFY_SSL, current_verify_ssl))
            timeout = float(user_input.get(CONF_REQUEST_TIMEOUT, current_timeout))

            client = CompanionApiClient(
                session=async_get_clientsession(self.hass),
                base_url=companion_url,
                access_token=access_token,
                key_id=current_key_id,
                refresh_token=current_refresh,
                access_token_expires_at=current_access_expires,
                refresh_token_expires_at=current_refresh_expires,
                verify_ssl=verify_ssl,
                request_timeout=timeout,
            )
            try:
                await client.async_get_health()
                await client.async_get_state()
                auth_details = await client.async_get_auth_status(auth=True)
            except CompanionAuthError:
                errors["base"] = "invalid_auth"
            except CompanionApiError as err:
                errors["base"] = _map_error(err)
            else:
                stored_access = _auth_value(auth_details, "access_token") or access_token
                stored_key = _auth_value(auth_details, "key_id") or current_key_id
                stored_refresh = _auth_value(auth_details, "refresh_token") or current_refresh
                stored_access_exp = _auth_value(auth_details, "access_token_expires_at") or current_access_expires
                stored_refresh_exp = _auth_value(auth_details, "refresh_token_expires_at") or current_refresh_expires

                return self.async_create_entry(
                    title="",
                    data={
                        CONF_COMPANION_URL: companion_url,
                        CONF_ACCESS_TOKEN: stored_access,
                        CONF_KEY_ID: stored_key,
                        CONF_REFRESH_TOKEN: stored_refresh,
                        CONF_ACCESS_TOKEN_EXPIRES_AT: stored_access_exp,
                        CONF_REFRESH_TOKEN_EXPIRES_AT: stored_refresh_exp,
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


def _reconfigure_schema(
    *,
    companion_url: str,
    verify_ssl: bool,
    timeout: float,
    user_input: Mapping[str, Any] | None,
) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_COMPANION_URL,
                default=str(user_input.get(CONF_COMPANION_URL, companion_url)),
            ): str,
            vol.Optional(
                CONF_ACCESS_TOKEN,
                default=str(user_input.get(CONF_ACCESS_TOKEN, "")),
            ): str,
            vol.Optional(
                CONF_CLAIM_CODE,
                default=str(user_input.get(CONF_CLAIM_CODE, "")),
            ): str,
            vol.Required(
                CONF_VERIFY_SSL,
                default=bool(user_input.get(CONF_VERIFY_SSL, verify_ssl)),
            ): bool,
            vol.Required(
                CONF_REQUEST_TIMEOUT,
                default=float(user_input.get(CONF_REQUEST_TIMEOUT, timeout)),
            ): vol.Coerce(float),
        }
    )


def _zeroconf_confirm_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_ACCESS_TOKEN, default=""): str,
            vol.Optional(CONF_CLAIM_CODE, default=""): str,
        }
    )


def _txt_property(properties: Mapping[Any, Any], key: str) -> str | None:
    raw = properties.get(key)
    if raw is None:
        raw = properties.get(key.encode())
    if raw is None:
        return None
    if isinstance(raw, bytes):
        value = raw.decode(errors="ignore").strip()
    else:
        value = str(raw).strip()
    return value or None


def _companion_url_from_discovery(discovery_info: ZeroconfServiceInfo) -> str | None:
    host = (
        _txt_property(discovery_info.properties, "host")
        or _txt_property(discovery_info.properties, "ip")
        or _txt_property(discovery_info.properties, "address")
    )
    if not host:
        ip_addr = discovery_info.ip_address
        if isinstance(ip_addr, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            host = str(ip_addr)
        elif ip_addr:
            host = str(ip_addr)
    if not host:
        return None

    scheme = (_txt_property(discovery_info.properties, "scheme") or "http").strip().lower()
    if scheme not in {"http", "https"}:
        scheme = "http"

    port = discovery_info.port or 8080
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    return _normalize_url(f"{scheme}://{host}:{port}")


def _map_error(err: CompanionApiError) -> str:
    code = (err.code or "").strip().lower()

    if code in {
        "token_required",
        "unauthorized",
        "invalid_auth",
        "token_expired",
        "refresh_token_expired",
        "invalid_refresh_token",
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


def _auth_value(payload: Mapping[str, Any], key: str) -> str:
    return str(payload.get(key, "")).strip()


def _auth_bool(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
