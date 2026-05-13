"""Async client for BTicino Companion API."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import (
    ClientConnectionError,
    ClientConnectorDNSError,
    ClientError,
    ClientResponse,
    ClientSession,
    ServerTimeoutError,
)

API_RETRY_ATTEMPTS = 3
API_RETRY_BASE_DELAY_SECONDS = 0.35


class CompanionApiError(Exception):
    """Raised for companion API failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        retryable: bool | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


class CompanionAuthError(CompanionApiError):
    """Raised for companion authentication failures."""


class CompanionApiClient:
    """Thin Companion API client."""

    def __init__(
        self,
        *,
        session: ClientSession,
        base_url: str,
        access_token: str,
        key_id: str = "",
        refresh_token: str = "",
        access_token_expires_at: str = "",
        refresh_token_expires_at: str = "",
        verify_ssl: bool,
        request_timeout: float,
        auth_state_listener: Callable[[dict[str, str]], Awaitable[None]] | None = None,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token.strip()
        self._key_id = key_id.strip()
        self._refresh_token = refresh_token.strip()
        self._access_token_expires_at = access_token_expires_at.strip()
        self._refresh_token_expires_at = refresh_token_expires_at.strip()
        self._verify_ssl = verify_ssl
        self._request_timeout = request_timeout
        self._auth_state_listener = auth_state_listener
        self._refresh_lock = asyncio.Lock()

    def update_runtime_config(
        self,
        *,
        base_url: str,
        access_token: str,
        verify_ssl: bool,
        request_timeout: float,
        key_id: str | None = None,
        refresh_token: str | None = None,
        access_token_expires_at: str | None = None,
        refresh_token_expires_at: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token.strip()
        self._verify_ssl = verify_ssl
        self._request_timeout = request_timeout
        if key_id is not None:
            self._key_id = key_id.strip()
        if refresh_token is not None:
            self._refresh_token = refresh_token.strip()
        if access_token_expires_at is not None:
            self._access_token_expires_at = access_token_expires_at.strip()
        if refresh_token_expires_at is not None:
            self._refresh_token_expires_at = refresh_token_expires_at.strip()

    async def async_get_health(self) -> dict[str, Any]:
        return await self._async_request("GET", "/api/v2/health", auth=False)

    async def async_get_auth_status(self, *, auth: bool | None = None) -> dict[str, Any]:
        if auth is None:
            auth = bool(self._access_token)
        payload = await self._async_request("GET", "/api/v2/auth/status", auth=auth)
        if auth:
            await self._async_apply_auth_payload(payload)
        return payload

    async def async_pair_challenge(self) -> dict[str, Any]:
        return await self._async_request("POST", "/api/v2/pair/challenge", auth=False)

    async def async_pair_claim(self, *, challenge_id: str, nonce: str, claim_code: str) -> dict[str, Any]:
        payload = {
            "challenge_id": challenge_id,
            "nonce": nonce,
            "claim_code": claim_code,
        }
        response = await self._async_request("POST", "/api/v2/pair/claim", auth=False, json_body=payload)
        await self._async_apply_auth_payload(response)
        return response

    async def async_auth_rotate(self) -> dict[str, Any]:
        payload = await self._async_request("POST", "/api/v2/auth/rotate", auth=True)
        await self._async_apply_auth_payload(payload)
        return payload

    async def async_auth_revoke(self, *, key_id: str) -> dict[str, Any]:
        payload = {"key_id": key_id}
        response = await self._async_request("POST", "/api/v2/auth/revoke", auth=True, json_body=payload)
        await self._async_apply_auth_payload(response)
        return response

    async def async_auth_refresh(self) -> dict[str, Any]:
        refresh_token = self._refresh_token.strip()
        if not refresh_token:
            raise CompanionAuthError("refresh token is required", code="refresh_token_required", status=401)

        payload = await self._async_request(
            "POST",
            "/api/v2/auth/refresh",
            auth=False,
            json_body={"refresh_token": refresh_token},
            allow_refresh=False,
        )
        await self._async_apply_auth_payload(payload)
        return payload

    async def async_issue_repair_code(self) -> dict[str, Any]:
        return await self._async_request("POST", "/api/v2/admin/issue-repair-code", auth=True)

    async def async_reset_claim(self, repair_code: str) -> dict[str, Any]:
        payload = {"repair_code": repair_code}
        return await self._async_request("POST", "/api/v2/admin/reset-claim", auth=True, json_body=payload)

    async def async_get_state(self) -> dict[str, Any]:
        return await self._async_request("GET", "/api/v2/state", auth=True)

    async def async_get_entrypoints(self) -> dict[str, Any]:
        return await self._async_request("GET", "/api/v2/entrypoints", auth=True)

    async def async_get_capabilities(self) -> dict[str, Any]:
        return await self._async_request("GET", "/api/v2/capabilities", auth=True)

    async def async_call_answer(self) -> dict[str, Any]:
        return await self._async_request("POST", "/api/v2/control/call/answer", auth=True)

    async def async_call_hangup(self) -> dict[str, Any]:
        return await self._async_request("POST", "/api/v2/control/call/hangup", auth=True)

    async def async_audio_mute(self) -> dict[str, Any]:
        return await self._async_request("POST", "/api/v2/control/audio/mute", auth=True)

    async def async_audio_unmute(self) -> dict[str, Any]:
        return await self._async_request("POST", "/api/v2/control/audio/unmute", auth=True)

    async def async_voicemail_enable(self) -> dict[str, Any]:
        return await self._async_request("POST", "/api/v2/control/voicemail/enable", auth=True)

    async def async_voicemail_disable(self) -> dict[str, Any]:
        return await self._async_request("POST", "/api/v2/control/voicemail/disable", auth=True)

    async def async_entrypoint_unlock(self, entrypoint_id: str) -> dict[str, Any]:
        return await self._async_request(
            "POST",
            f"/api/v2/control/entrypoints/{entrypoint_id}/unlock",
            auth=True,
        )

    async def async_entrypoint_stream_start(self, entrypoint_id: str) -> dict[str, Any]:
        return await self._async_request(
            "POST",
            f"/api/v2/control/entrypoints/{entrypoint_id}/stream/start",
            auth=True,
        )

    async def async_entrypoint_stream_stop(self, entrypoint_id: str) -> dict[str, Any]:
        return await self._async_request(
            "POST",
            f"/api/v2/control/entrypoints/{entrypoint_id}/stream/stop",
            auth=True,
        )

    async def async_open_events_stream(self, *, last_event_id: int | None = None) -> ClientResponse:
        if not self._access_token and not await self._async_try_refresh():
            raise CompanionAuthError("access token is required for events stream")

        params: dict[str, str] = {}
        if isinstance(last_event_id, int) and last_event_id > 0:
            params["last_event_id"] = str(last_event_id)

        url = f"{self._base_url}/api/v2/events"
        attempted_refresh = False
        while True:
            headers = {
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self._access_token}",
            }
            try:
                response = await self._session.get(
                    url,
                    headers=headers,
                    params=params,
                    ssl=self._verify_ssl,
                    timeout=None,
                )
            except (
                ClientConnectorDNSError,
                ClientConnectionError,
                ServerTimeoutError,
                asyncio.TimeoutError,
                OSError,
            ) as err:
                raise CompanionApiError(f"network error while opening events stream {url}") from err
            except ClientError as err:
                raise CompanionApiError(f"http client error while opening events stream {url}") from err

            if response.status in (401, 403):
                payload: Any
                try:
                    payload = await response.json(content_type=None)
                except Exception:  # noqa: BLE001
                    payload = {}
                response.release()

                parsed = self._parse_error(payload, response.status)
                if not attempted_refresh and self._can_refresh(parsed):
                    attempted_refresh = True
                    if await self._async_try_refresh():
                        continue

                raise CompanionAuthError(
                    parsed["message"],
                    code=parsed["code"],
                    status=parsed["status"],
                    retryable=parsed["retryable"],
                    retry_after=parsed["retry_after"],
                )

            if response.status >= 400:
                body = await response.text()
                response.release()
                raise CompanionApiError(f"companion events stream failed ({response.status}): {body[:180]}")

            return response

    async def async_open_openwebnet_trace_stream(self, *, last_event_id: int | None = None) -> ClientResponse:
        if not self._access_token and not await self._async_try_refresh():
            raise CompanionAuthError("access token is required for openwebnet trace stream")

        params: dict[str, str] = {}
        if isinstance(last_event_id, int) and last_event_id > 0:
            params["last_event_id"] = str(last_event_id)

        url = f"{self._base_url}/api/v2/trace/openwebnet/stream"
        attempted_refresh = False
        while True:
            headers = {
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self._access_token}",
            }
            try:
                response = await self._session.get(
                    url,
                    headers=headers,
                    params=params,
                    ssl=self._verify_ssl,
                    timeout=None,
                )
            except (
                ClientConnectorDNSError,
                ClientConnectionError,
                ServerTimeoutError,
                asyncio.TimeoutError,
                OSError,
            ) as err:
                raise CompanionApiError(f"network error while opening openwebnet trace stream {url}") from err
            except ClientError as err:
                raise CompanionApiError(f"http client error while opening openwebnet trace stream {url}") from err

            if response.status in (401, 403):
                payload: Any
                try:
                    payload = await response.json(content_type=None)
                except Exception:  # noqa: BLE001
                    payload = {}
                response.release()

                parsed = self._parse_error(payload, response.status)
                if not attempted_refresh and self._can_refresh(parsed):
                    attempted_refresh = True
                    if await self._async_try_refresh():
                        continue

                raise CompanionAuthError(
                    parsed["message"],
                    code=parsed["code"],
                    status=parsed["status"],
                    retryable=parsed["retryable"],
                    retry_after=parsed["retry_after"],
                )

            if response.status >= 400:
                body = await response.text()
                response.release()
                raise CompanionApiError(f"companion openwebnet trace stream failed ({response.status}): {body[:180]}")

            return response

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        auth: bool,
        json_body: dict[str, Any] | None = None,
        allow_refresh: bool = True,
    ) -> dict[str, Any]:
        if auth and not self._access_token and not (allow_refresh and await self._async_try_refresh()):
            raise CompanionAuthError("access token is required")

        headers = {
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"Bearer {self._access_token}"

        url = f"{self._base_url}{path}"
        attempted_refresh = False

        for attempt in range(1, API_RETRY_ATTEMPTS + 1):
            try:
                async with asyncio.timeout(self._request_timeout):
                    response = await self._session.request(
                        method,
                        url,
                        headers=headers,
                        json=json_body,
                        ssl=self._verify_ssl,
                    )
            except (
                ClientConnectorDNSError,
                ClientConnectionError,
                ServerTimeoutError,
                asyncio.TimeoutError,
                OSError,
            ) as err:
                if attempt >= API_RETRY_ATTEMPTS:
                    raise CompanionApiError(f"network error while requesting {url}") from err
                await asyncio.sleep(API_RETRY_BASE_DELAY_SECONDS * attempt)
                continue
            except ClientError as err:
                raise CompanionApiError(f"http client error while requesting {url}") from err

            payload: Any
            try:
                payload = await response.json(content_type=None)
            except Exception:  # noqa: BLE001
                payload = {}

            if response.status >= 400:
                parsed = self._parse_error(payload, response.status)
                if (
                    auth
                    and allow_refresh
                    and response.status in (401, 403)
                    and not attempted_refresh
                    and self._can_refresh(parsed)
                ):
                    attempted_refresh = True
                    if await self._async_try_refresh():
                        headers["Authorization"] = f"Bearer {self._access_token}"
                        continue

                if response.status in (401, 403):
                    raise CompanionAuthError(
                        parsed["message"],
                        code=parsed["code"],
                        status=parsed["status"],
                        retryable=parsed["retryable"],
                        retry_after=parsed["retry_after"],
                    )
                raise CompanionApiError(
                    parsed["message"],
                    code=parsed["code"],
                    status=parsed["status"],
                    retryable=parsed["retryable"],
                    retry_after=parsed["retry_after"],
                )

            if not isinstance(payload, dict):
                raise CompanionApiError("companion returned non-object payload")
            return payload

        raise CompanionApiError(f"request retries exhausted for {url}")

    def _can_refresh(self, parsed_error: dict[str, Any]) -> bool:
        if not self._refresh_token:
            return False
        status = parsed_error.get("status")
        if status not in (401, 403):
            return False
        code = str(parsed_error.get("code") or "").strip().lower()
        if code in {"invalid_refresh_token", "refresh_token_expired", "refresh_token_required"}:
            return False
        return True

    async def _async_try_refresh(self) -> bool:
        if not self._refresh_token:
            return False
        async with self._refresh_lock:
            if not self._refresh_token:
                return False
            try:
                await self.async_auth_refresh()
            except CompanionApiError:
                return False
            return True

    async def _async_apply_auth_payload(self, payload: dict[str, Any]) -> None:
        updates: dict[str, str] = {}

        access_token = str(payload.get("access_token", "")).strip()
        if access_token and access_token != self._access_token:
            self._access_token = access_token
            updates["access_token"] = self._access_token

        refresh_token = str(payload.get("refresh_token", "")).strip()
        if refresh_token and refresh_token != self._refresh_token:
            self._refresh_token = refresh_token
            updates["refresh_token"] = self._refresh_token

        key_id = str(payload.get("key_id", "")).strip()
        if key_id and key_id != self._key_id:
            self._key_id = key_id
            updates["key_id"] = self._key_id

        access_token_expires_at = str(payload.get("access_token_expires_at", "")).strip()
        if access_token_expires_at and access_token_expires_at != self._access_token_expires_at:
            self._access_token_expires_at = access_token_expires_at
            updates["access_token_expires_at"] = self._access_token_expires_at

        refresh_token_expires_at = str(payload.get("refresh_token_expires_at", "")).strip()
        if refresh_token_expires_at and refresh_token_expires_at != self._refresh_token_expires_at:
            self._refresh_token_expires_at = refresh_token_expires_at
            updates["refresh_token_expires_at"] = self._refresh_token_expires_at

        if updates and self._auth_state_listener is not None:
            await self._auth_state_listener(updates)

    @staticmethod
    def _parse_error(payload: Any, fallback_status: int) -> dict[str, Any]:
        message = f"companion request failed ({fallback_status})"
        code = None
        retryable = fallback_status >= 500
        retry_after = None
        status = fallback_status

        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                if isinstance(error.get("message"), str) and error["message"].strip():
                    message = error["message"].strip()
                if isinstance(error.get("code"), str) and error["code"].strip():
                    code = error["code"].strip()
                if isinstance(error.get("status"), int):
                    status = error["status"]
                if isinstance(error.get("retryable"), bool):
                    retryable = error["retryable"]
                if isinstance(error.get("retry_after"), int):
                    retry_after = error["retry_after"]

        return {
            "message": message,
            "code": code,
            "status": status,
            "retryable": retryable,
            "retry_after": retry_after,
        }
