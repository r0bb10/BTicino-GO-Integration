"""Constants for BTicino Intercom integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "bticino_intercom"
NAME = "BTicino Intercom"

CONF_COMPANION_URL = "companion_url"
CONF_ACCESS_TOKEN = "access_token"
CONF_CLAIM_CODE = "claim_code"
CONF_VERIFY_SSL = "verify_ssl"
CONF_REQUEST_TIMEOUT = "request_timeout_sec"

DEFAULT_COMPANION_URL = "http://127.0.0.1:8080"
DEFAULT_ACCESS_TOKEN = ""
DEFAULT_VERIFY_SSL = False
DEFAULT_REQUEST_TIMEOUT = 8.0

COORDINATOR_UPDATE_INTERVAL = timedelta(seconds=20)
COMMAND_TIMEOUT_SECONDS = 8.0
SSE_READLINE_TIMEOUT_SECONDS = 30.0
SSE_STALE_THRESHOLD_SECONDS = 45.0

PLATFORMS: list[str] = ["sensor", "binary_sensor"]

SERVICE_REFRESH = "refresh"
SERVICE_CALL_ANSWER = "call_answer"
SERVICE_CALL_HANGUP = "call_hangup"
SERVICE_ENTRYPOINT_UNLOCK = "entrypoint_unlock"
SERVICE_ENTRYPOINT_STREAM_START = "entrypoint_stream_start"
SERVICE_ENTRYPOINT_STREAM_STOP = "entrypoint_stream_stop"

DATA_SERVICES_REGISTERED = f"{DOMAIN}_services_registered"
