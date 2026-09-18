"""Native MediaKit standard/generative video enhancement; no paid retries."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import configuration_status, get_config
from .mediakit import BASE_URL, MediaKitClient, MediaKitError as EnhanceError, valid_url

SOURCES = ["https://www.volcengine.com/docs/6448/" + doc for doc in
           ("2279230", "2464595", "2536891", "2278532", "2300661")]
ENDPOINTS = {"standard": "/api/v1/tools/enhance-video",
             "generative": "/api/v1/tools/enhance-video-generative"}


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


class EnhanceClient(MediaKitClient):
    async def create(self, request: EnhanceRequest) -> dict:
        endpoint, body = prepare_enhance(request)
        data = await self._request("POST", endpoint, body)
        if not isinstance(data.get("task_id"), str) or not data["task_id"]:
            raise EnhanceError("MediaKit response has no task_id; submission outcome uncertain. Do not submit another task.")
        return {"variant": request.variant, "task":data, "paid_request_sent":True,
                "client_token":request.client_token, "next_step":"Query task.task_id with video_enhance_get_task. Do not recreate."}
