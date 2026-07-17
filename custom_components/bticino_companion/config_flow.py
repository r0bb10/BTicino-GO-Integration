"""Configuration flow for BTicino Companion v3."""

from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import logging
import re
from typing import Any

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
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    NAME,
)

_LOGGER = logging.getLogger(__name__)

_HOSTNAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)


class CompanionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a Companion using its stable device ID."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_device_id: str | None = None
        self._discovered_url: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await self._async_pair(user_input)
            except CompanionApiError as err:
                _LOGGER.warning("Companion pairing failed: %s", type(err).__name__)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(data[CONF_DEVICE_ID])
                self._abort_if_unique_id_configured(updates={CONF_COMPANION_URL: data[CONF_COMPANION_URL]})
                return self.async_create_entry(title=f"{NAME} ({data[CONF_DEVICE_ID]})", data=data)
        return self.async_show_form(step_id="user", data_schema=_user_schema(user_input), errors=errors)

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        device_id = _txt(discovery_info.properties, "device_id")
        discovered_url = _zeroconf_url(
            getattr(discovery_info, "host", None), getattr(discovery_info, "port", None)
        )
        if not device_id or not discovered_url:
            return self.async_abort(reason="cannot_connect")
        self._discovered_device_id = device_id
        self._discovered_url = discovered_url
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured(updates={CONF_COMPANION_URL: self._discovered_url})
        self.context["title_placeholders"] = {"name": _txt(discovery_info.properties, "name") or device_id}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._discovered_device_id is None or self._discovered_url is None:
            return self.async_abort(reason="cannot_connect")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await self._async_pair(
                    {
                        **user_input,
                        CONF_DEVICE_ID: self._discovered_device_id,
                        CONF_COMPANION_URL: self._discovered_url,
                        CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
                    }
                )
            except CompanionApiError as err:
                _LOGGER.warning("Companion pairing failed: %s", type(err).__name__)
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=f"{NAME} ({data[CONF_DEVICE_ID]})", data=data)
        return self.async_show_form(
            step_id="zeroconf_confirm", data_schema=_claim_schema(), errors=errors
        )

    async def _async_pair(self, user_input: Mapping[str, Any]) -> dict[str, Any]:
        device_id = str(user_input.get(CONF_DEVICE_ID, "")).strip()
        base_url = _normalize_url(str(user_input.get(CONF_COMPANION_URL, "")))
        claim_code = str(user_input.get(CONF_CLAIM_CODE, "")).strip()
        verify_ssl = bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
        if not device_id or not base_url or not claim_code:
            raise CompanionApiError("device ID, URL, and claim code are required")
        client = CompanionApiClient(async_get_clientsession(self.hass), base_url, "", verify_ssl)
        _LOGGER.debug("Requesting Companion pairing challenge")
        challenge = await client.async_pair_challenge()
        _LOGGER.debug("Submitting Companion pairing claim")
        await client.async_pair_claim(
            challenge_id=str(challenge.get("challenge_id", "")),
            claim_code=claim_code,
        )
        _LOGGER.info("Companion pairing completed")
        return {
            CONF_DEVICE_ID: device_id,
            CONF_COMPANION_URL: base_url,
            CONF_ACCESS_TOKEN: client.access_token,
            CONF_VERIFY_SSL: verify_ssl,
        }


def _user_schema(user_input: Mapping[str, Any] | None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(CONF_DEVICE_ID, default=str(user_input.get(CONF_DEVICE_ID, ""))): str,
            vol.Required(CONF_COMPANION_URL, default=str(user_input.get(CONF_COMPANION_URL, ""))): str,
            vol.Required(CONF_CLAIM_CODE, default=str(user_input.get(CONF_CLAIM_CODE, ""))): str,
            vol.Required(CONF_VERIFY_SSL, default=bool(user_input.get(CONF_VERIFY_SSL, False))): bool,
        }
    )


def _claim_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_CLAIM_CODE, default=""): str,
        }
    )


def _normalize_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if value and "://" not in value:
        return f"http://{value}"
    return value


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
