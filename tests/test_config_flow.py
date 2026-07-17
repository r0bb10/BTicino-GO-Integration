"""Tests for Companion config flow Zeroconf discovery."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from bticino_companion.config_flow import CompanionConfigFlow, _zeroconf_url
    from bticino_companion.const import (
        CONF_ACCESS_TOKEN,
        CONF_COMPANION_URL,
        CONF_DEVICE_ID,
        CONF_REPAIR_CODE,
    )
except ImportError as err:
    if "homeassistant" not in str(err):
        raise
    raise unittest.SkipTest("homeassistant is not installed") from err


class ZeroconfUrlTest(unittest.TestCase):
    def test_uses_normalized_discovered_hostname_and_port(self) -> None:
        self.assertEqual(_zeroconf_url(" Companion.Local. ", 8181), "http://companion.local:8181")

    def test_uses_bracketed_ipv6_address(self) -> None:
        self.assertEqual(_zeroconf_url("2001:0db8::1", 8080), "http://[2001:db8::1]:8080")

    def test_rejects_missing_or_invalid_address_data(self) -> None:
        self.assertIsNone(_zeroconf_url(None, 8080))
        self.assertIsNone(_zeroconf_url("companion.local", 0))
        self.assertIsNone(_zeroconf_url("http://companion.local", 8080))


class ZeroconfConfigFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_existing_entry_updates_only_discovered_url(self) -> None:
        flow = MagicMock()
        flow.context = {}
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_step_zeroconf_confirm = AsyncMock(return_value={"type": "form"})
        flow.async_step_zeroconf_recover = AsyncMock()
        discovery_info = SimpleNamespace(
            properties={"device_id": "device-id", "name": "Companion"},
            host="192.0.2.42",
            port=8181,
        )

        result = await CompanionConfigFlow.async_step_zeroconf(flow, discovery_info)

        self.assertEqual(result, {"type": "form"})
        flow.async_set_unique_id.assert_awaited_once_with("device-id")
        flow._abort_if_unique_id_configured.assert_called_once_with(
            updates={CONF_COMPANION_URL: "http://192.0.2.42:8181"}
        )
        flow.async_step_zeroconf_recover.assert_not_awaited()

    async def test_claimed_discovery_starts_recovery_flow(self) -> None:
        flow = MagicMock()
        flow.context = {}
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_step_zeroconf_confirm = AsyncMock()
        flow.async_step_zeroconf_recover = AsyncMock(return_value={"type": "form"})
        discovery_info = SimpleNamespace(
            properties={"device_id": "device-id", "name": "Companion", "needs_claim": "false"},
            host="192.0.2.42",
            port=8181,
        )

        result = await CompanionConfigFlow.async_step_zeroconf(flow, discovery_info)

        self.assertEqual(result, {"type": "form"})
        flow.async_step_zeroconf_confirm.assert_not_awaited()
        flow.async_step_zeroconf_recover.assert_awaited_once()

    async def test_recovery_exchanges_repair_code_for_bearer(self) -> None:
        flow = MagicMock()
        flow.hass = MagicMock()
        client = MagicMock(access_token="replacement-bearer")
        client.async_recover_bearer = AsyncMock()

        with (
            patch("bticino_companion.config_flow.async_get_clientsession", return_value=MagicMock()),
            patch("bticino_companion.config_flow.CompanionApiClient", return_value=client),
        ):
            result = await CompanionConfigFlow._async_authorize(
                flow,
                {
                    CONF_DEVICE_ID: "device-id",
                    CONF_COMPANION_URL: "http://192.0.2.42:8080",
                    CONF_REPAIR_CODE: "abcd-1234",
                },
                recovery=True,
            )

        client.async_recover_bearer.assert_awaited_once_with("abcd-1234")
        self.assertEqual(
            result,
            {
                CONF_DEVICE_ID: "device-id",
                CONF_COMPANION_URL: "http://192.0.2.42:8080",
                CONF_ACCESS_TOKEN: "replacement-bearer",
                "verify_ssl": False,
            },
        )

    async def test_invalid_discovered_address_aborts(self) -> None:
        flow = MagicMock()
        flow.async_abort.return_value = {"type": "abort", "reason": "cannot_connect"}
        discovery_info = SimpleNamespace(
            properties={"device_id": "device-id"}, host="", port=8080
        )

        result = await CompanionConfigFlow.async_step_zeroconf(flow, discovery_info)

        self.assertEqual(result, {"type": "abort", "reason": "cannot_connect"})
        flow.async_set_unique_id.assert_not_called()
