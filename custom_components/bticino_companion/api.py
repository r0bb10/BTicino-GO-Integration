"""REST client for Companion pairing, WebRTC, and snapshots."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession

from .const import (
    API_PATH_PAIR_CHALLENGE,
    API_PATH_PAIR_CLAIM,
    API_PATH_SNAPSHOT_CAPTURE,
    API_PATH_SNAPSHOT_LATEST,
    API_PATH_WEBRTC_CANDIDATE,
    API_PATH_WEBRTC_CLOSE,
    API_PATH_WEBRTC_OFFER,
)


class CompanionApiError(Exception):
    """Raised when a Companion REST request fails."""


class CompanionAuthError(CompanionApiError):
    """Raised when a Companion request is unauthorized."""


class CompanionApiClient:
    """REST operations that are not available through the WebSocket protocol."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        access_token: str = "",
        verify_ssl: bool = False,
    ) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token.strip()
        self._verify_ssl = verify_ssl

    @property
    def access_token(self) -> str:
        """Return the active bearer token."""
        return self._access_token

    @property
    def session(self) -> ClientSession:
        """Return the shared Home Assistant HTTP session."""
        return self._session

    @property
    def base_url(self) -> str:
        """Return the current Companion base URL."""
        return self._base_url

    @property
    def verify_ssl(self) -> bool:
        """Return the configured TLS verification setting."""
        return self._verify_ssl

    def update_runtime_config(self, *, base_url: str, access_token: str, verify_ssl: bool) -> None:
        """Update config-entry values without recreating the shared client."""
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token.strip()
        self._verify_ssl = verify_ssl

    async def async_pair_challenge(self) -> dict[str, Any]:
        return await self._async_request("POST", API_PATH_PAIR_CHALLENGE, auth=False)

    async def async_pair_claim(
        self, *, challenge_id: str, nonce: str, claim_code: str
    ) -> dict[str, Any]:
        payload = await self._async_request(
            "POST",
            API_PATH_PAIR_CLAIM,
            auth=False,
            json_body={
                "challenge_id": challenge_id,
                "nonce": nonce,
                "claim_code": claim_code,
            },
        )
        access_token = str(payload.get("access_token", "")).strip()
        if not access_token:
            raise CompanionApiError("pair claim response did not contain an access token")
        self._access_token = access_token
        return payload

    async def async_webrtc_offer(
        self, *, entrypoint_id: str, offer_sdp: str, session_id: str
    ) -> dict[str, Any]:
        return await self._async_request(
            "POST",
            API_PATH_WEBRTC_OFFER,
            auth=True,
            json_body={
                "entrypoint_id": entrypoint_id,
                "offer_sdp": offer_sdp,
                "session_id": session_id,
            },
        )

    async def async_webrtc_candidate(
        self, *, session_id: str, candidate: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._async_request(
            "POST",
            API_PATH_WEBRTC_CANDIDATE,
            auth=True,
            json_body={"session_id": session_id, "candidate": candidate},
        )

    async def async_webrtc_close(self, *, session_id: str) -> dict[str, Any]:
        return await self._async_request(
            "POST", API_PATH_WEBRTC_CLOSE, auth=True, json_body={"session_id": session_id}
        )

    async def async_capture_snapshot(self, entrypoint_id: str) -> bytes:
        return await self._async_request_bytes(
            "POST", API_PATH_SNAPSHOT_CAPTURE.format(entrypoint_id=entrypoint_id)
        )

    async def async_get_latest_snapshot(self, entrypoint_id: str) -> bytes:
        return await self._async_request_bytes(
            "GET", API_PATH_SNAPSHOT_LATEST.format(entrypoint_id=entrypoint_id)
        )

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        auth: bool,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._async_open_request(method, path, auth=auth, json_body=json_body)
        async with response:
            payload = await self._async_json(response)
            self._raise_for_status(response, payload)
            if not isinstance(payload, dict):
                raise CompanionApiError("Companion returned a non-object response")
            return payload

    async def _async_request_bytes(self, method: str, path: str) -> bytes:
        response = await self._async_open_request(method, path, auth=True)
        async with response:
            if response.status >= 400:
                self._raise_for_status(response, await self._async_json(response))
            payload = await response.read()
            if not payload:
                raise CompanionApiError("Companion returned an empty snapshot")
            return payload

    async def _async_open_request(
        self,
        method: str,
        path: str,
        *,
        auth: bool,
        json_body: dict[str, Any] | None = None,
    ) -> ClientResponse:
        if auth and not self._access_token:
            raise CompanionAuthError("an access token is required")
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self._access_token}"
        try:
            return await self._session.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                json=json_body,
                ssl=self._verify_ssl,
            )
        except ClientError as err:
            raise CompanionApiError("unable to contact Companion") from err

    @staticmethod
    async def _async_json(response: ClientResponse) -> Any:
        try:
            return await response.json(content_type=None)
        except (ClientError, ValueError):
            return {}

    @staticmethod
    def _raise_for_status(response: ClientResponse, payload: Any) -> None:
        if response.status < 400:
            return
        message = f"Companion request failed ({response.status})"
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            message = str(payload["error"].get("message", message))
        if response.status in (401, 403):
            raise CompanionAuthError(message)
        raise CompanionApiError(message)
