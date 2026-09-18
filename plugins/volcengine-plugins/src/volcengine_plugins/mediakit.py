"""Shared MediaKit transport, safe errors and unchanged local-video uploads."""
from __future__ import annotations

import mimetypes
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from .config import get_config

BASE_URL = "https://mediakit.cn-beijing.volces.com"


def valid_url(value: str, schemes: set[str]) -> bool:
    try:
        p = urlsplit(value)
        return p.scheme in schemes and bool(p.hostname) and not p.username and not p.password and not p.fragment
    except ValueError:
        return False


class MediaKitError(RuntimeError):
    pass


class MediaKitClient:
    def __init__(self, *, transport=None, upload_transport=None):
        self._key = get_config("MEDIAKIT_API_KEY").strip()
        self._base = get_config("MEDIAKIT_BASE_URL", BASE_URL).rstrip("/")
        if not valid_url(self._base, {"https"}) or urlsplit(self._base).query:
            raise ValueError("MEDIAKIT_BASE_URL must be an administrator-configured HTTPS URL")
        try:
            self._timeout = float(get_config("MEDIAKIT_TIMEOUT_SECONDS", "120"))
        except ValueError:
            raise ValueError("MEDIAKIT_TIMEOUT_SECONDS must be 1..300") from None
        if not 1 <= self._timeout <= 300:
            raise ValueError("MEDIAKIT_TIMEOUT_SECONDS must be 1..300")
        self._transport = transport
        self._upload_transport = upload_transport

    async def _request(self, method: str, endpoint: str, body=None) -> dict:
        if not self._key:
            raise MediaKitError("MEDIAKIT_API_KEY is not configured. ARK_API_KEY is not used as a fallback.")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout, follow_redirects=False) as client:
                r = await client.request(method, self._base + endpoint,
                    headers={"Authorization": "Bearer " + self._key}, **({"json":body} if body is not None else {}))
        except httpx.HTTPError:
            raise MediaKitError("MediaKit network failure; submission outcome may be unknown. No automatic retry. Retain client_token/task_id.") from None
        try:
            data = r.json()
        except ValueError:
            raise MediaKitError(f"MediaKit HTTP {r.status_code}: invalid JSON; no automatic retry") from None
        if not isinstance(data, dict):
            raise MediaKitError("MediaKit returned an invalid response object; no automatic retry")
        if not r.is_success or data.get("success") is not True:
            error = data.get("error")
            code = str(error.get("code", "UnknownError")) if isinstance(error, dict) else "ProviderError"
            code = code if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", code) and self._key not in code else "ProviderError"
            # Never surface raw provider messages: they may echo keys, signed URLs or request bodies.
            raise MediaKitError(f"MediaKit HTTP {r.status_code}: {code}; no automatic retry")
        if data.get("error"):
            data["error"] = {"code": "TaskFailed", "message": "Provider task failed; inspect its request/task ID in MediaKit console."}
        return data

    async def get(self, task_id: str) -> dict:
        if not re.fullmatch(r"amk-tool-[A-Za-z0-9_-]{1,200}", task_id):
            raise ValueError("Invalid MediaKit task_id")
        return {"task":await self._request("GET", "/api/v1/tasks/" + task_id), "paid_request_sent":False}

    async def upload(self, file_path: str) -> dict:
        path = Path(file_path)
        extensions = {".mp4", ".mov", ".m4v", ".mkv", ".flv", ".ts", ".avi", ".wmv", ".webm"}
        if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in extensions:
            raise ValueError("Upload requires an existing absolute path to a supported video file")
        size = path.stat().st_size
        if not 0 < size <= 10 * 1024**3:
            raise ValueError("Video must be nonempty and at most 10 GiB")
        data = await self._request("POST", "/api/v1/tools-sync/request-media-upload-url", {})
        result = data.get("result") or {}
        uri = result.get("file_id", "")
        url = result.get("upload_url", "")
        if not isinstance(uri,str) or not uri or not valid_url(url, {"https"}) or result.get("method") != "PUT":
            raise MediaKitError("MediaKit returned invalid upload credentials")
        headers = result.get("upload_headers") or []
        if not isinstance(headers,list) or any(not isinstance(h,dict) or not isinstance(h.get("key"),str) or not isinstance(h.get("value"),str) for h in headers):
            raise MediaKitError("Invalid provider upload headers")
        headers = {h["key"]:h["value"] for h in headers}
        if not any(k.lower()=="content-type" for k in headers):
            headers["Content-Type"] = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        # The bearer key is never forwarded to the upload host. Stream file bytes unchanged.
        headers["Content-Length"] = str(size)
        async def chunks():
            with path.open("rb") as f:
                while chunk := f.read(1024*1024):
                    yield chunk
        try:
            async with httpx.AsyncClient(transport=self._upload_transport,timeout=self._timeout,follow_redirects=False) as client:
                response = await client.put(url, headers=headers, content=chunks())
        except (httpx.HTTPError, OSError):
            raise MediaKitError("MediaKit file upload failed; no processing task was submitted") from None
        if not response.is_success:
            raise MediaKitError(f"MediaKit upload HTTP {response.status_code}; no processing task submitted")
        return {"video_url":uri if uri.startswith("mediakit://") else "mediakit://"+uri,
                "bytes":size,"paid_request_sent":False,"uploaded":True,
                "note":"File uploaded unchanged. No processing task submitted; storage/transfer charges, if any, follow provider billing."}
