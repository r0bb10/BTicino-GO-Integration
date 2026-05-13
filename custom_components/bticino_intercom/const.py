"""Constants for BTicino Intercom integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "bticino_intercom"
NAME = "BTicino Intercom"

CONF_COMPANION_URL = "companion_url"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_KEY_ID = "key_id"
CONF_ACCESS_TOKEN_EXPIRES_AT = "access_token_expires_at"
CONF_REFRESH_TOKEN_EXPIRES_AT = "refresh_token_expires_at"
CONF_CLAIM_CODE = "claim_code"
CONF_VERIFY_SSL = "verify_ssl"
CONF_REQUEST_TIMEOUT = "request_timeout_sec"

DEFAULT_COMPANION_URL = "http://127.0.0.1:8080"
DEFAULT_ACCESS_TOKEN = ""
DEFAULT_VERIFY_SSL = False
DEFAULT_REQUEST_TIMEOUT = 8.0

COORDINATOR_UPDATE_INTERVAL = timedelta(seconds=20)
COMMAND_TIMEOUT_SECONDS = 8.0
SSE_READLINE_TIMEOUT_SECONDS = 12.0
SSE_HEARTBEAT_TIMEOUT_SECONDS = 20.0
SSE_AVAILABILITY_GRACE_SECONDS = 35.0
SSE_RECONNECT_MIN_SECONDS = 1.0
SSE_RECONNECT_MAX_SECONDS = 20.0
SSE_RECONNECT_JITTER_SECONDS = 0.35

PLATFORMS: list[str] = ["sensor", "binary_sensor", "button", "camera", "event", "switch"]

SERVICE_REFRESH = "refresh"
SERVICE_CALL_ANSWER = "call_answer"
SERVICE_CALL_HANGUP = "call_hangup"
SERVICE_AUDIO_MUTE = "audio_mute"
SERVICE_AUDIO_UNMUTE = "audio_unmute"
SERVICE_VOICEMAIL_ENABLE = "voicemail_enable"
SERVICE_VOICEMAIL_DISABLE = "voicemail_disable"
SERVICE_ENTRYPOINT_UNLOCK = "entrypoint_unlock"
SERVICE_ENTRYPOINT_STREAM_START = "entrypoint_stream_start"
SERVICE_ENTRYPOINT_STREAM_STOP = "entrypoint_stream_stop"

DATA_SERVICES_REGISTERED = f"{DOMAIN}_services_registered"
ISSUE_CLAIM_RECOVERY = "claim_recovery"
EVENT_OPENWEBNET_FRAME = "bticino_intercom_openwebnet_frame"
SIGNAL_OPENWEBNET_TRACE = f"{DOMAIN}_openwebnet_trace"
