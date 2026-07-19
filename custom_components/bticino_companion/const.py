"""Constants for the BTicino Companion integration."""

from __future__ import annotations

DOMAIN = "bticino_companion"
NAME = "BTicino Companion"
DATA_CAMERA_ENTITIES = f"{DOMAIN}_camera_entities"
DATA_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"
DATA_PENDING_REAUTH_URLS = f"{DOMAIN}_pending_reauth_urls"
FRONTEND_PATH = "/bticino_companion_static"
CARD_RESOURCE_URL = f"{FRONTEND_PATH}/bticino-go-intercom-card.js?automatically-added"

PLATFORMS: list[str] = [
    "camera",
    "sensor",
    "binary_sensor",
    "button",
    "switch",
    "update",
]

CONF_ACCESS_TOKEN = "access_token"
CONF_CLAIM_CODE = "claim_code"
CONF_COMPANION_URL = "companion_url"
CONF_DEVICE_ID = "device_id"
CONF_INSTANCE_ID = "instance_id"
CONF_REPAIR_CODE = "repair_code"
DEFAULT_PORT = 8080

API_PATH_PAIR_CHALLENGE = "/api/v3/pair/challenge"
API_PATH_PAIR_CLAIM = "/api/v3/pair/claim"
API_PATH_RECOVER_BEARER = "/api/v3/auth/recover"
API_PATH_AUTH_STATUS = "/api/v3/auth/status"
API_PATH_ENTRYPOINT_UNLOCK = "/api/v3/entrypoints/{entrypoint_id}/unlock"
API_PATH_AUDIO_MUTE = "/api/v3/audio/mute"
API_PATH_AUDIO_UNMUTE = "/api/v3/audio/unmute"
API_PATH_VOICEMAIL_ENABLE = "/api/v3/voicemail/enable"
API_PATH_VOICEMAIL_DISABLE = "/api/v3/voicemail/disable"
API_PATH_UPDATE_INSTALL = "/api/v3/system/update/install"
API_PATH_SYSTEM_REBOOT = "/api/v3/system/reboot"
API_PATH_SYSTEM_SERVICE_RESTART = "/api/v3/system/services/{service}/restart"
API_PATH_WEBRTC_OFFER = "/api/v3/webrtc/offer"
API_PATH_WEBRTC_CANDIDATE = "/api/v3/webrtc/candidate"
API_PATH_WEBRTC_CLOSE = "/api/v3/webrtc/close"
API_PATH_SNAPSHOT_LATEST = "/api/v3/entrypoints/{entrypoint_id}/snapshot/latest.jpg"
WEBSOCKET_PATH = "/api/v3/ws"

WEBSOCKET_PING_INTERVAL_SECONDS = 25
WEBSOCKET_RECONNECT_MIN_SECONDS = 1
WEBSOCKET_RECONNECT_MAX_SECONDS = 30
WEBSOCKET_CONNECT_TIMEOUT_SECONDS = 10
