from __future__ import annotations

import json
import hashlib
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

# Shared across client instances in this MCP process. Never retain access tokens.
_read_rate_lock = threading.Lock()
_read_last: dict[tuple[str, str], float] = {}


def _pace_read(token: str, path: str) -> None:
    key = (hashlib.sha256(token.encode()).hexdigest(), path)
    with _read_rate_lock:
        now = time.monotonic()
        delay = 0.65 - (now - _read_last.get(key, 0))
        if delay > 0:
            time.sleep(delay)
        _read_last[key] = time.monotonic()
        if len(_read_last) > 1024:
            cutoff = time.monotonic() - 60
            for old in list(_read_last):
                if _read_last[old] < cutoff:
                    del _read_last[old]


@dataclass(frozen=True)
class QianchuanConfig:
    api_base_url: str
    timeout_seconds: float = 30.0
    allowed_advertiser_ids: frozenset[str] = field(default_factory=frozenset)
    trust_env: bool = False


class QianchuanApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: int | str | None = None,
        request_id: str | None = None,
        status_code: int | None = None,
        response_payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id
        self.status_code = status_code
        self.response_payload = response_payload or {}


class QianchuanClient:
    def __init__(self, config: QianchuanConfig) -> None:
        self.config = config

    def get(self, path: str, *, access_token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in range(3):
            _pace_read(access_token, path)
            try:
                return self._request("GET", path, access_token=access_token, params=params)
            except QianchuanApiError as exc:
                retryable = str(exc.code) in {"40110", "50000"} or exc.status_code in {429, 502, 503, 504}
                if not retryable or attempt == 2:
                    raise
                time.sleep(1.0 * (2 ** attempt))
        raise AssertionError("unreachable")

    def post(
        self,
        path: str,
        *,
        access_token: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, access_token=access_token, json_body=json_body)

    def upload_video(
        self,
        path: str,
        *,
        access_token: str,
        advertiser_id: int,
        file_path: Path,
        filename: str,
        video_signature: str,
        content_type: str,
        is_aigc: bool = False,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Upload one local video using the official multipart endpoint."""
        allowlist_payload = {"advertiser_id": advertiser_id}
        self._enforce_advertiser_allowlist(
            path,
            params=allowlist_payload,
            json_body=None,
        )
        form_data = {
            "advertiser_id": str(advertiser_id),
            "filename": filename,
            "is_aigc": "true" if is_aigc else "false",
            "upload_type": "UPLOAD_BY_FILE",
            "video_signature": video_signature,
        }
        if labels:
            form_data["labels"] = json.dumps(labels, ensure_ascii=False, separators=(",", ":"))
        headers = {"Access-Token": access_token}
        try:
            with file_path.open("rb") as source:
                files = {"video_file": (file_path.name, source, content_type)}
                with httpx.Client(
                    timeout=self.config.timeout_seconds,
                    trust_env=self.config.trust_env,
                ) as client:
                    response = client.post(
                        f"{self.config.api_base_url.rstrip('/')}/{path.lstrip('/')}",
                        headers=headers,
                        data=form_data,
                        files=files,
                    )
        except (OSError, httpx.HTTPError) as exc:
            raise QianchuanApiError(f"Qianchuan video upload failed: {exc}") from exc
        return _validate_api_response(response)

    def upload_image(
        self,
        path: str,
        *,
        access_token: str,
        advertiser_id: int,
        file_path: Path,
        filename: str,
        image_signature: str,
        content_type: str,
        is_aigc: bool,
    ) -> dict[str, Any]:
        """Upload one local image using the official multipart endpoint."""
        self._enforce_advertiser_allowlist(path, params={"advertiser_id": advertiser_id}, json_body=None)
        form_data = {
            "advertiser_id": str(advertiser_id),
            "filename": filename,
            "image_signature": image_signature,
            "is_aigc": "true" if is_aigc else "false",
            "upload_type": "UPLOAD_BY_FILE",
        }
        try:
            with file_path.open("rb") as source:
                files = {"image_file": (file_path.name, source, content_type)}
                with httpx.Client(timeout=self.config.timeout_seconds, trust_env=self.config.trust_env) as client:
                    response = client.post(
                        f"{self.config.api_base_url.rstrip('/')}/{path.lstrip('/')}",
                        headers={"Access-Token": access_token},
                        data=form_data,
                        files=files,
                    )
        except (OSError, httpx.HTTPError) as exc:
            raise QianchuanApiError(f"Qianchuan image upload failed: {exc}") from exc
        return _validate_api_response(response)

    def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._enforce_advertiser_allowlist(path, params=params, json_body=json_body)
        headers = {"Content-Type": "application/json", "Access-Token": access_token}
        try:
            with httpx.Client(
                timeout=self.config.timeout_seconds,
                trust_env=self.config.trust_env,
            ) as client:
                response = client.request(
                    method,
                    f"{self.config.api_base_url.rstrip('/')}/{path.lstrip('/')}",
                    headers=headers,
                    params=_serialize_params(params or {}),
                    json=_jsonable(json_body or {}) if json_body is not None else None,
                )
        except httpx.HTTPError as exc:
            raise QianchuanApiError(f"Qianchuan API request failed: {exc}") from exc
        return _validate_api_response(response)

    def _enforce_advertiser_allowlist(
        self,
        path: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
    ) -> None:
        allowed_ids = self.config.allowed_advertiser_ids
        if not allowed_ids:
            return
        normalized_path = "/" + path.lstrip("/")
        if normalized_path == "/oauth2/advertiser/get/":
            return
        advertiser_ids = _extract_advertiser_ids(params or {}) | _extract_advertiser_ids(
            json_body or {}
        )
        if not advertiser_ids:
            raise QianchuanApiError(
                "Qianchuan request is missing advertiser_id; hard account allowlist blocked it."
            )
        unexpected = advertiser_ids - allowed_ids
        if unexpected:
            raise QianchuanApiError(
                "Qianchuan advertiser hard allowlist blocked account(s): "
                + ",".join(sorted(unexpected))
            )


def _serialize_params(params: dict[str, Any]) -> dict[str, str]:
    serialized: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple)):
            serialized[key] = json.dumps(_jsonable(value), ensure_ascii=True, separators=(",", ":"))
            continue
        if isinstance(value, bool):
            serialized[key] = "true" if value else "false"
            continue
        serialized[key] = str(_jsonable(value))
    return serialized


def _load_json_response(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        body_preview = response.text.strip().replace("\r", " ").replace("\n", " ")[:200]
        detail = f" (HTTP {response.status_code})"
        if body_preview:
            detail += f": {body_preview}"
        raise QianchuanApiError(
            "Qianchuan API returned invalid JSON" + detail,
            status_code=response.status_code,
        ) from exc
    if not isinstance(payload, dict):
        raise QianchuanApiError("Qianchuan API returned a non-object response.")
    return payload


def _validate_api_response(response: httpx.Response) -> dict[str, Any]:
    payload = _load_json_response(response)
    if response.status_code >= 400:
        raise QianchuanApiError(
            str(payload.get("message") or f"HTTP {response.status_code}"),
            code=payload.get("code"),
            request_id=payload.get("request_id"),
            status_code=response.status_code,
            response_payload=payload,
        )
    if "code" in payload and str(payload.get("code")) != "0":
        raise QianchuanApiError(
            str(payload.get("message") or payload.get("msg") or "Qianchuan API error."),
            code=payload.get("code"),
            request_id=payload.get("request_id"),
            status_code=response.status_code,
            response_payload=payload,
        )
    return payload


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _extract_advertiser_ids(value: Any, *, key: str | None = None) -> set[str]:
    if isinstance(value, dict):
        found: set[str] = set()
        for child_key, child_value in value.items():
            found.update(_extract_advertiser_ids(child_value, key=str(child_key)))
        return found
    if key in {"advertiser_id", "advertiser_ids"}:
        items = value if isinstance(value, (list, tuple, set)) else [value]
        return {str(item) for item in items if item is not None}
    if isinstance(value, (list, tuple, set)):
        found: set[str] = set()
        for item in value:
            found.update(_extract_advertiser_ids(item))
        return found
    return set()
