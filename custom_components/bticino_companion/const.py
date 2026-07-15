"""Constants for the BTicino Companion integration."""

from __future__ import annotations

DOMAIN = "bticino_companion"
NAME = "BTicino Companion"

PLATFORMS: list[str] = [
    "sensor",
    "binary_sensor",
    "button",
    "switch",
    "camera",
    "event",
    "update",
]

CONF_ACCESS_TOKEN = "access_token"
CONF_CLAIM_CODE = "claim_code"
CONF_COMPANION_URL = "companion_url"
CONF_DEVICE_ID = "device_id"
CONF_VERIFY_SSL = "verify_ssl"

DEFAULT_VERIFY_SSL = False
DEFAULT_PORT = 8080

API_PATH_PAIR_CHALLENGE = "/api/v3/pair/challenge"
API_PATH_PAIR_CLAIM = "/api/v3/pair/claim"
API_PATH_ISSUE_REPAIR_CODE = "/api/v3/admin/issue-repair-code"
API_PATH_RESET_CLAIM = "/api/v3/admin/reset-claim"
API_PATH_WEBRTC_OFFER = "/api/v3/webrtc/offer"
API_PATH_WEBRTC_CANDIDATE = "/api/v3/webrtc/candidate"
API_PATH_WEBRTC_CLOSE = "/api/v3/webrtc/close"
API_PATH_SNAPSHOT_CAPTURE = "/api/v3/control/entrypoints/{entrypoint_id}/snapshot"
API_PATH_SNAPSHOT_LATEST = "/api/v3/entrypoints/{entrypoint_id}/snapshot/latest.jpg"
WEBSOCKET_PATH = "/api/v3/ws"

WEBSOCKET_PING_INTERVAL_SECONDS = 25
WEBSOCKET_RECONNECT_MIN_SECONDS = 1
WEBSOCKET_RECONNECT_MAX_SECONDS = 30
WEBSOCKET_CONNECT_TIMEOUT_SECONDS = 10

ISSUE_CLAIM_RECOVERY = "claim_recovery"
