"""Feishu HTTP transport with independent capability enforcement and token caching."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import re
import time
from typing import Any

import httpx

from .settings import Access, ConfigurationError, Settings

_ID = r"[A-Za-z0-9_-]+"
_SHEETS = rf"/open-apis/sheets/v[23]/spreadsheets/{_ID}"
_TABLE = rf"/open-apis/bitable/v1/apps/{_ID}/tables"
_RECORDS = rf"{_TABLE}/{_ID}/records"
# Operation semantics, not the HTTP verb alone: Bitable search uses POST but is read-only.
_READ = [
    ("GET", r"/open-apis/bot/v3/info"),
    ("GET", rf"/open-apis/im/v1/chats(?:/{_ID})?"),
    ("GET", rf"/open-apis/im/v1/messages(?:/{_ID})?"),
    ("GET", rf"{_SHEETS}(?:/sheets/query|/values/[A-Za-z0-9_%!:$.-]+)?"),
    ("GET", rf"{_TABLE}(?:/{_ID}/fields)?"),
    ("GET", rf"{_RECORDS}/{_ID}"),
    ("POST", rf"{_RECORDS}/search"),
]
_WRITE = [
    ("POST", rf"/open-apis/im/v1/messages(?:/{_ID}/reply)?"),
    ("DELETE", rf"/open-apis/im/v1/messages/{_ID}"),
    ("PUT", rf"{_SHEETS}/(?:values|dimension_range|style)"),
    ("POST", rf"{_SHEETS}/(?:values_append|sheets_batch_update|dimension_range)"),
    ("POST", rf"{_RECORDS}/batch_(?:create|update|delete)"),
]


class FeishuError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: int | str | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
        outcome: str = "rejected",
    ):
        super().__init__(message)
        self.code, self.http_status, self.request_id, self.outcome = code, http_status, request_id, outcome

    def as_dict(self) -> dict:
        return {
            "message": str(self),
            "code": self.code,
            "http_status": self.http_status,
            "request_id": self.request_id,
            "outcome": self.outcome,
        }


def operation_access(method: str, path: str) -> Access:
    for access, rules in (("read", _READ), ("write", _WRITE)):
        if any(method == verb and re.fullmatch(pattern, path) for verb, pattern in rules):
            return access  # type: ignore[return-value]
    raise ValueError("该 API 不在插件固定接口清单中。")


class FeishuClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._http = httpx.AsyncClient(
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "feishu-bot-plugin/1.0.0"},
        )
        self._token = ""
        self._token_expires = 0.0
        self._token_lock = asyncio.Lock()
        # Serializes route/append operations within this server process.
        self.sheet_write_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    def redact(self, text: str) -> str:
        for secret in (
            self.settings.app_secret,
            self.settings.tenant_access_token,
            self.settings.webhook_url,
            self.settings.webhook_secret,
            self._token,
        ):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text[:1500]

    async def _access_token(self) -> str:
        if self.settings.tenant_access_token:
            return self.settings.tenant_access_token
        if not (self.settings.app_id and self.settings.app_secret):
            raise ConfigurationError(
                "请在外部环境中配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET，或 FEISHU_TENANT_ACCESS_TOKEN。"
            )
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires:
                return self._token
            try:
                response = await self._http.post(
                    self.settings.api_base_url + "/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": self.settings.app_id, "app_secret": self.settings.app_secret},
                )
            except httpx.RequestError:
                raise FeishuError("获取应用凭据失败，请检查网络后重试。", outcome="not_sent") from None
            payload = self._decode(response, "read")
            token = payload.get("tenant_access_token")
            if not isinstance(token, str) or not token:
                raise FeishuError("鉴权响应缺少 tenant_access_token。", outcome="not_sent")
            self._token = token
            self._token_expires = time.monotonic() + max(0, float(payload.get("expire", 0)) - 120)
            return token

    def _decode(self, response: httpx.Response, access: Access) -> dict:
        request_id = response.headers.get("x-tt-logid") or response.headers.get("x-request-id")
        uncertain = access == "write" and response.status_code >= 500
        if not 200 <= response.status_code < 300:
            raise FeishuError(
                "飞书 HTTP 请求失败。" + ("写入结果未知，请先查询确认，不要直接重放。" if uncertain else ""),
                http_status=response.status_code,
                request_id=request_id,
                outcome="unknown" if uncertain else "rejected",
            )
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            raise FeishuError(
                "飞书返回了非 JSON 响应。",
                http_status=response.status_code,
                request_id=request_id,
                outcome="unknown" if access == "write" else "rejected",
            ) from None
        if not isinstance(payload, dict):
            raise FeishuError("飞书响应结构异常。", outcome="unknown" if access == "write" else "rejected")
        code = payload.get("code", payload.get("StatusCode"))
        if code is None or code not in (0, "0"):
            raise FeishuError(
                self.redact(str(payload.get("msg", payload.get("StatusMessage", "飞书业务请求失败。")))),
                code=code,
                http_status=response.status_code,
                request_id=request_id,
                outcome="unknown" if access == "write" and code is None else "rejected",
            )
        return payload

    async def request(
        self, method: str, path: str, *, params: dict | None = None, body: dict | None = None
    ) -> dict[str, Any]:
        access = operation_access(method, path)
        self.settings.require(access)  # Before authentication or ANY network request.
        refreshed = False
        for attempt in range(3 if access == "read" else 1):
            token = await self._access_token()
            try:
                response = await self._http.request(
                    method,
                    self.settings.api_base_url + path,
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                    json=body,
                )
            except httpx.RequestError:
                if access == "read" and attempt < 2:
                    await asyncio.sleep(0.25 * 2**attempt)
                    continue
                raise FeishuError(
                    "网络请求失败。" + ("写入结果未知，请查询确认后再决定是否重试。" if access == "write" else ""),
                    outcome="unknown" if access == "write" else "not_sent",
                ) from None
            try:
                payload = self._decode(response, access)
                return payload.get("data", payload)
            except FeishuError as exc:
                if access != "read" or attempt >= 2:
                    raise
                if (exc.http_status == 401 or exc.code in (99991661, 99991663, 99991664, 99991668)) and (
                    not refreshed and not self.settings.tenant_access_token
                ):
                    self._token_expires = 0
                    refreshed = True
                    continue
                if exc.http_status == 429 or (exc.http_status is not None and exc.http_status >= 500):
                    try:
                        delay = min(5.0, max(0.25, float(response.headers.get("retry-after", "0.25"))))
                    except ValueError:
                        delay = 0.25
                    await asyncio.sleep(delay * 2**attempt)
                    continue
                raise
        raise FeishuError("读取重试次数已用尽。")

    async def webhook(self, payload: dict) -> dict:
        self.settings.require("write")
        if not self.settings.webhook_url:
            raise ConfigurationError("请在外部环境中配置 FEISHU_WEBHOOK_URL。")
        body = dict(payload)
        if self.settings.webhook_secret:
            timestamp = str(int(time.time()))
            digest = hmac.new(f"{timestamp}\n{self.settings.webhook_secret}".encode(), b"", hashlib.sha256).digest()
            body.update(timestamp=timestamp, sign=base64.b64encode(digest).decode())
        try:
            response = await self._http.post(self.settings.webhook_url, json=body)
        except httpx.RequestError:
            raise FeishuError(
                "Webhook 请求失败，发送结果未知；请核实群消息后再决定是否重试。", outcome="unknown"
            ) from None
        return self._decode(response, "write")
