"""Configuration and reauthentication flow for BTicino Companion v3."""

from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import CompanionApiClient, CompanionApiError
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_CLAIM_CODE,
    CONF_COMPANION_URL,
    CONF_DEVICE_ID,
    CONF_INSTANCE_ID,
    CONF_REPAIR_CODE,
    DATA_PENDING_REAUTH_URLS,
    DEFAULT_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_HOSTNAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)
_PAIRING_STATES = frozenset({"setup_required", "claimable", "claimed", "error"})


class CompanionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a Companion using its physical device ID and installation ID."""

    VERSION = 1

    def __init__(self) -> None:
        self._device_id = ""
        self._url = ""
        self._model = ""
        self._instance_id = ""
        self._pairing_state = "error"
        self._is_reauth = False

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Start manual setup by looking up the Companion's public state."""
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = _normalize_url(str(user_input.get(CONF_COMPANION_URL, "")))
            try:
                await self._async_load_pairing_status(base_url)
            except CompanionApiError as err:
                _LOGGER.warning("Companion pairing status lookup failed: %s", type(err).__name__)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(self._device_id)
                self._abort_if_unique_id_configured(updates={CONF_COMPANION_URL: self._url})
                return await self._async_step_for_pairing_state()
        return self.async_show_form(step_id="user", data_schema=_user_schema(user_input), errors=errors)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Use Companion mDNS data to select the initial authorization flow."""
        device_id = _txt(discovery_info.properties, "device_id")
        discovered_url = _zeroconf_url(
            getattr(discovery_info, "host", None), getattr(discovery_info, "port", None)
        )
        pairing_state = _txt(discovery_info.properties, "pairing_state")
        if not device_id or not discovered_url or pairing_state not in _PAIRING_STATES:
            return self.async_abort(reason="cannot_connect")

        try:
            self._set_pairing_status(
                {
                    CONF_DEVICE_ID: device_id,
                    "model": _txt(discovery_info.properties, "model"),
                    CONF_INSTANCE_ID: _txt(discovery_info.properties, "instance_id"),
                    "pairing_state": pairing_state,
                },
                discovered_url,
            )
        except CompanionApiError:
            return self.async_abort(reason="cannot_connect")
        await self.async_set_unique_id(self._device_id)
        existing_entries = self._async_current_entries()
        if existing_entries:
            return await self._async_handle_existing_discovery(existing_entries[0])
        self.context["title_placeholders"] = {"name": self._model or self._device_id}
        return await self._async_step_for_pairing_state()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Select claim or recovery after Home Assistant loses authorization."""
        self._is_reauth = True
        entry = self._get_reauth_entry()
        pending_urls = self.hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PENDING_REAUTH_URLS, {}
        )
        base_url = pending_urls.pop(
            entry.entry_id, _normalize_url(str(entry_data.get(CONF_COMPANION_URL, "")))
        )
        try:
            await self._async_load_pairing_status(base_url)
        except CompanionApiError as err:
            _LOGGER.warning("Companion reauthentication status lookup failed: %s", type(err).__name__)
            return self.async_abort(reason="cannot_connect")

        if self._device_id != str(entry_data.get(CONF_DEVICE_ID, "")).strip():
            return self.async_abort(reason="wrong_device")
        await self.async_set_unique_id(self._device_id)
        return await self._async_step_for_pairing_state()

    async def _async_handle_existing_discovery(self, entry: Any) -> ConfigFlowResult:
        stored_instance_id = str(entry.data.get(CONF_INSTANCE_ID, "")).strip()
        if stored_instance_id == self._instance_id and self._pairing_state == "claimed":
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_COMPANION_URL: self._url}
            )
            runtime = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
            if runtime is not None:
                await runtime.async_update_base_url(self._url)
            return self.async_abort(reason="already_configured")

        pending_urls = self.hass.data.setdefault(DOMAIN, {}).setdefault(
            DATA_PENDING_REAUTH_URLS, {}
        )
        pending_urls[entry.entry_id] = self._url
        entry.async_start_reauth(self.hass)
        return self.async_abort(reason="reauth_started")

    async def async_step_claim(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Claim an unpaired Companion with its owner-visible initial code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await self._async_authorize(str(user_input.get(CONF_CLAIM_CODE, "")), recovery=False)
            except CompanionApiError as err:
                _LOGGER.warning("Companion initial claim failed: %s", type(err).__name__)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish_authorization(data)
        return self.async_show_form(step_id="claim", data_schema=_claim_schema(), errors=errors)

    async def async_step_recover(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Recover an already-claimed Companion with an owner-issued code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await self._async_authorize(str(user_input.get(CONF_REPAIR_CODE, "")), recovery=True)
            except CompanionApiError as err:
                _LOGGER.warning("Companion credential recovery failed: %s", type(err).__name__)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish_authorization(data)
        return self.async_show_form(step_id="recover", data_schema=_repair_schema(), errors=errors)

    async def _async_step_for_pairing_state(self) -> ConfigFlowResult:
        if self._pairing_state == "claimable":
            return await self.async_step_claim()
        if self._pairing_state == "claimed":
            return await self.async_step_recover()
        if self._pairing_state == "setup_required":
            return self.async_abort(reason="setup_required")
        return self.async_abort(reason="pairing_error")

    async def _async_load_pairing_status(self, base_url: str) -> None:
        if not base_url:
            raise CompanionApiError("Companion URL is required")
        client = CompanionApiClient(async_get_clientsession(self.hass), base_url)
        status = await client.async_get_pairing_status()
        self._set_pairing_status(status, base_url)

    def _set_pairing_status(self, status: Mapping[str, Any], base_url: str) -> None:
        device_id = str(status.get(CONF_DEVICE_ID, "")).strip()
        instance_id = str(status.get(CONF_INSTANCE_ID, "")).strip()
        pairing_state = str(status.get("pairing_state", "")).strip()
        if not device_id or not instance_id or pairing_state not in _PAIRING_STATES:
            raise CompanionApiError("Companion returned an invalid pairing status")
        self._device_id = device_id
        self._url = base_url
        self._model = str(status.get("model", "")).strip()
        self._instance_id = instance_id
        self._pairing_state = pairing_state

    async def _async_authorize(self, code: str, *, recovery: bool) -> dict[str, Any]:
        code = code.strip()
        if not self._device_id or not self._url or not code:
            raise CompanionApiError("Companion authorization details are required")
        client = CompanionApiClient(
            async_get_clientsession(self.hass), self._url
        )
        if recovery:
            await client.async_recover_bearer(code)
        else:
            challenge = await client.async_pair_challenge()
            await client.async_pair_claim(
                challenge_id=str(challenge.get("challenge_id", "")), claim_code=code
            )
        return {
            CONF_DEVICE_ID: self._device_id,
            CONF_COMPANION_URL: self._url,
            CONF_ACCESS_TOKEN: client.access_token,
            CONF_INSTANCE_ID: self._instance_id,
        }

    async def _async_finish_authorization(self, data: dict[str, Any]) -> ConfigFlowResult:
        await self.async_set_unique_id(self._device_id)
        if self._is_reauth:
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        self._abort_if_unique_id_configured(updates={CONF_COMPANION_URL: self._url})
        return self.async_create_entry(title=self._model or self._device_id, data=data)


def _user_schema(user_input: Mapping[str, Any] | None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_COMPANION_URL, default=str(user_input.get(CONF_COMPANION_URL, ""))
            ): str,
        }
    )


def _claim_schema() -> vol.Schema:
    return vol.Schema({vol.Required(CONF_CLAIM_CODE, default=""): str})


def _repair_schema() -> vol.Schema:
    return vol.Schema({vol.Required(CONF_REPAIR_CODE, default=""): str})


def _normalize_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None:
        return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    return urlunsplit((parsed.scheme, f"{host}:{DEFAULT_PORT}", "", "", ""))


def _zeroconf_url(host: Any, port: Any) -> str | None:
    """Return a safe direct HTTP URL from Zeroconf service data."""
    if not isinstance(host, str) or not isinstance(port, int) or isinstance(port, bool):
        return None
    if not 1 <= port <= 65535:
        return None

    host = host.strip()
    if host.endswith("."):
        host = host[:-1]
    if not host:
        return None

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME_RE.fullmatch(host):
            return None
        return f"http://{host.lower()}:{port}"

    if address.version == 6:
        return f"http://[{address.compressed}]:{port}"
    return f"http://{address.compressed}:{port}"


def _txt(properties: Mapping[Any, Any], key: str) -> str:
    value = properties.get(key, properties.get(key.encode()))
    if isinstance(value, bytes):
        return value.decode(errors="ignore").strip()
    return str(value).strip() if value is not None else ""
