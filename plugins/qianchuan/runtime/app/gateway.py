from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.oauth_session import selected_token


class TokenResolutionError(RuntimeError):
    pass


class OAuthGatewayClient:
    def __init__(self, settings: Settings, *, token_no: str | None = None) -> None:
        if token_no is not None and not re.fullmatch(r"qcot_[A-Za-z0-9_-]+", token_no):
            raise ValueError("Invalid token identifier")
        self.settings = settings.model_copy(update={
            "oauth_gateway_token_no": token_no or selected_token(settings)
        })

    def get_access_token(self) -> str:
        payload = self.get_access_token_response()
        access_token = payload.get("access_token")
        if not access_token:
            raise TokenResolutionError("OAuth gateway response is missing access_token.")
        return str(access_token)

    def get_access_token_response(self) -> dict[str, Any]:
        """Return the gateway's selected-token response without exposing signing details."""
        if not (
            self.settings.oauth_gateway_base_url
            and self.settings.oauth_gateway_token_no
            and self.settings.oauth_gateway_hmac_secret
        ):
            raise TokenResolutionError("OAuth gateway token source is not configured.")
        path = f"/internal/tokens/{self.settings.oauth_gateway_token_no}/access-token"
        response = self._signed_request("POST", path, {})
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TokenResolutionError(f"OAuth gateway returned {response.status_code}: {response.text}") from exc
        payload = response.json()
        if not isinstance(payload, dict):
            raise TokenResolutionError("OAuth gateway returned a non-object token response.")
        return payload

    def get_resolved_advertisers(self) -> dict[str, Any]:
        """List the Qianchuan business accounts resolved by the selected gateway token.

        This deliberately uses the gateway response rather than the upstream OAuth
        root-account endpoint, which may contain shop/agent management accounts.
        """
        payload = self.get_access_token_response()
        advertisers = payload.get("advertisers")
        if not isinstance(advertisers, list):
            raise TokenResolutionError("OAuth gateway response is missing advertisers.")
        return {
            "code": 0,
            "message": "OK",
            "data": {"list": advertisers},
            "source": "oauth_gateway_resolved",
            "token_no": payload.get("token_no"),
        }

    def create_start_url(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_base_auth_config()
        response = self._signed_request("POST", "/internal/oauth/start-url", payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TokenResolutionError(f"OAuth gateway returned {response.status_code}: {response.text}") from exc
        return response.json()

    def list_authorizations(self) -> dict[str, Any]:
        self._ensure_base_auth_config()
        response = self._signed_request("GET", "/internal/authorizations", {})
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TokenResolutionError(f"OAuth gateway returned {response.status_code}: {response.text}") from exc
        return response.json()

    def _signed_request(self, method: str, path: str, payload: dict[str, Any]) -> httpx.Response:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        parsed = urlsplit(path)
        body_hash = hashlib.sha256(body).hexdigest()
        canonical = "\n".join([timestamp, nonce, method.upper(), parsed.path, parsed.query, body_hash])
        signature = "sha256=" + hmac.new(
            str(self.settings.oauth_gateway_hmac_secret).encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        with httpx.Client(
            timeout=30,
            trust_env=self.settings.outbound_http_trust_env,
        ) as client:
            return client.request(
                method,
                f"{str(self.settings.oauth_gateway_base_url).rstrip('/')}{path}",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-QC-Timestamp": timestamp,
                    "X-QC-Nonce": nonce,
                    "X-QC-Signature": signature,
                },
            )

    def _ensure_base_auth_config(self) -> None:
        if not (self.settings.oauth_gateway_base_url and self.settings.oauth_gateway_hmac_secret):
            raise TokenResolutionError("OAuth gateway base URL/HMAC secret is not configured.")


def resolve_access_token(settings: Settings) -> str:
    if settings.qianchuan_access_token:
        return settings.qianchuan_access_token
    return OAuthGatewayClient(settings).get_access_token()
