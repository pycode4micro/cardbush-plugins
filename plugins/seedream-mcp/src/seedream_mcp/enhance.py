"""Native MediaKit standard/generative video enhancement; no paid retries."""
from __future__ import annotations

import mimetypes
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import configuration_status, get_config

BASE_URL = "https://mediakit.cn-beijing.volces.com"
SOURCES = ["https://www.volcengine.com/docs/6448/" + doc for doc in
           ("2279230", "2464595", "2536891", "2278532", "2300661")]
ENDPOINTS = {"standard": "/api/v1/tools/enhance-video",
             "generative": "/api/v1/tools/enhance-video-generative"}


def valid_url(value: str, schemes: set[str]) -> bool:
    try:
        p = urlsplit(value)
        return p.scheme in schemes and bool(p.hostname) and not p.username and not p.password and not p.fragment
    except ValueError:
        return False


class EnhanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    variant: Literal["standard", "generative"] = Field(default="standard", description="Only standard or generative. No fast/professional routes.")
    video_url: str = Field(min_length=1, description="HTTP(S), mediakit://, vod:// or tos:// source. Upload local files with video_enhance_upload first.")
    resolution: Literal["240p", "360p", "480p", "540p", "720p", "1080p", "2k", "4k", "8k"] | None = None
    resolution_limit: int | None = Field(default=None, ge=128, le=4320)
    scene: Literal["common", "ugc", "short_series", "aigc", "old_film"] | None = None
    enhance_style: Literal["hd", "natural"] | None = None
    bitrate_level: Literal["low", "medium", "high"] | None = None
    bitrate: int | None = Field(default=None, ge=10, le=150000, description="kbps; if supplied, provider ignores bitrate_level.")
    fps: float | None = Field(default=None, ge=15, le=120, description="Omit to preserve source frame rate. No duration or speed modification.")
    media_output_destination: str | None = None
    client_token: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[\x20-\x7e]+$", description="Native idempotency token. Reuse exactly this token for the same uncertain submission, never generate a new one to poll.")
    callback_args: str | None = None
    callback_url: str | None = None
    queue_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_native(self):
        if not valid_url(self.video_url, {"http", "https", "mediakit", "vod", "tos"}):
            raise ValueError("video_url must be a supported remote URI, not a local path or base64")
        if self.resolution is not None and self.resolution_limit is not None:
            raise ValueError("resolution and resolution_limit are mutually exclusive")
        if self.variant == "generative":
            if self.resolution not in {None, "720p", "1080p", "2k"}:
                raise ValueError("generative resolution must be 720p, 1080p or 2k")
            if any(x is not None for x in (self.scene, self.enhance_style, self.resolution_limit)):
                raise ValueError("scene, enhance_style and resolution_limit are standard-only")
        if self.callback_url is not None and not valid_url(self.callback_url, {"http", "https"}):
            raise ValueError("Invalid callback_url")
        if self.callback_args is not None and len(self.callback_args.encode("utf-8")) > 512:
            raise ValueError("callback_args exceeds 512 UTF-8 bytes")
        if self.media_output_destination is not None and not valid_url(self.media_output_destination, {"vod", "tos"}):
            raise ValueError("media_output_destination must be vod:// or tos://")
        return self


def prepare_enhance(request: EnhanceRequest) -> tuple[str, dict]:
    body = request.model_dump(exclude_none=True, exclude={"variant"})
    if request.variant == "standard":
        body["tool_version"] = "standard"
    return ENDPOINTS[request.variant], body


def enhance_preview(request: EnhanceRequest) -> dict:
    endpoint, body = prepare_enhance(request)
    for name in ("video_url", "callback_url", "callback_args", "media_output_destination", "client_token", "queue_id"):
        if name in body:
            body[name] = "[redacted]"
    warnings = ["Remote media dimensions, HDR/SDR, duration and account access are validated by MediaKit; no local media probe or upload is performed."]
    if request.bitrate is not None and request.bitrate_level is not None:
        warnings.append("Native bitrate overrides bitrate_level.")
    return {"endpoint": endpoint, "body": body, "warnings": warnings, "paid_request_sent": False}


def enhance_capabilities() -> dict:
    return {"variants": {"standard": {"resolutions": ["240p", "360p", "480p", "540p", "720p", "1080p", "2k", "4k", "8k"],
                "input_short_side": [360,1440], "input_long_side": [360,2560],
                "scenes": ["common", "ugc", "short_series", "aigc", "old_film"], "styles": ["hd", "natural"]},
            "generative": {"resolutions": ["720p", "1080p", "2k"], "default_resolution": "720p",
                "input_short_side": [360,1080], "input_long_side": [360,1920], "sdr_only": True}},
        "configured": bool(get_config("MEDIAKIT_API_KEY").strip()), "configuration": configuration_status(),
        "schema": EnhanceRequest.model_json_schema(), "sources": SOURCES,
        "notes": ["MEDIAKIT_API_KEY is independent from ARK_API_KEY; no automatic fallback.",
                  "No fast/professional endpoint, no extra_body escape hatch.",
                  "Upload transfers the requested file unchanged; create is a paid asynchronous request; get only queries.",
                  "Omit fps to preserve frame rate. No local scaling, muting, trimming, splitting or automatic retries.",
                  "Result URLs normally expire in 24 hours; task lookup covers 30 days.",
                  "Upload wrapper accepts supported video extensions up to 10 GiB; underlying provider validates media."]}


class EnhanceError(RuntimeError):
    pass


class EnhanceClient:
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
            raise EnhanceError("MEDIAKIT_API_KEY is not configured. ARK_API_KEY is not used as a fallback.")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout, follow_redirects=False) as client:
                r = await client.request(method, self._base + endpoint,
                    headers={"Authorization": "Bearer " + self._key}, **({"json":body} if body is not None else {}))
        except httpx.HTTPError:
            raise EnhanceError("MediaKit network failure; submission outcome may be unknown. No automatic retry. Retain client_token/task_id.") from None
        try:
            data = r.json()
        except ValueError:
            raise EnhanceError(f"MediaKit HTTP {r.status_code}: invalid JSON; no automatic retry") from None
        if not isinstance(data, dict):
            raise EnhanceError("MediaKit returned an invalid response object; no automatic retry")
        if not r.is_success or data.get("success") is not True:
            code = str((data.get("error") or {}).get("code", "UnknownError"))
            code = code if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", code) and self._key not in code else "ProviderError"
            # Never surface raw provider messages: they may echo keys, signed URLs or request bodies.
            raise EnhanceError(f"MediaKit HTTP {r.status_code}: {code}; no automatic retry")
        if data.get("error"):
            data["error"] = {"code": "TaskFailed", "message": "Provider task failed; inspect its request/task ID in MediaKit console."}
        return data

    async def create(self, request: EnhanceRequest) -> dict:
        endpoint, body = prepare_enhance(request)
        data = await self._request("POST", endpoint, body)
        if not isinstance(data.get("task_id"), str) or not data["task_id"]:
            raise EnhanceError("MediaKit response has no task_id; submission outcome uncertain. Do not submit another task.")
        return {"variant": request.variant, "task":data, "paid_request_sent":True,
                "client_token":request.client_token, "next_step":"Query task.task_id with video_enhance_get_task. Do not recreate."}

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
            raise EnhanceError("MediaKit returned invalid upload credentials")
        headers = result.get("upload_headers") or []
        if not isinstance(headers,list) or any(not isinstance(h,dict) or not isinstance(h.get("key"),str) or not isinstance(h.get("value"),str) for h in headers):
            raise EnhanceError("Invalid provider upload headers")
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
            raise EnhanceError("MediaKit file upload failed; no enhancement task was submitted") from None
        if not response.is_success:
            raise EnhanceError(f"MediaKit upload HTTP {response.status_code}; no enhancement task submitted")
        return {"video_url":uri if uri.startswith("mediakit://") else "mediakit://"+uri,
                "bytes":size,"paid_request_sent":False,"uploaded":True,
                "note":"File uploaded unchanged. No enhancement submitted; storage/transfer charges, if any, follow provider billing."}
