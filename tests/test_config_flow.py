"""Tests for Companion config flow Zeroconf discovery."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock

COMPONENT_PATH = Path(__file__).parents[1] / "custom_components"
sys.path.insert(0, str(COMPONENT_PATH))

try:
    from bticino_companion.config_flow import CompanionConfigFlow, _zeroconf_url
    from bticino_companion.const import CONF_COMPANION_URL
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

    async def test_invalid_discovered_address_aborts(self) -> None:
        flow = MagicMock()
        flow.async_abort.return_value = {"type": "abort", "reason": "cannot_connect"}
        discovery_info = SimpleNamespace(
            properties={"device_id": "device-id"}, host="", port=8080
        )

        result = await CompanionConfigFlow.async_step_zeroconf(flow, discovery_info)

        self.assertEqual(result, {"type": "abort", "reason": "cannot_connect"})
        flow.async_set_unique_id.assert_not_called()
