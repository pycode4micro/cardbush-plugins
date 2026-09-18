"""Official MediaKit fine subtitle erasure; no translation or video regeneration."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import configuration_status, get_config
from .mediakit import MediaKitClient, MediaKitError, valid_url
from .task_io import download

ENDPOINT = "/api/v1/tools/erase-video-subtitle-pro"
SOURCES = [f"https://www.volcengine.com/docs/6448/{doc}" for doc in
           ("2372084", "2371372", "2536891", "2278532", "2300661")]
Ratio = Annotated[float, Field(ge=0, le=1)]


class NativeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True, allow_inf_nan=False)


class EraseLocation(NativeModel):
    """Normalized rectangle, including the complete subtitle outline and shadow."""

    top_left_x: Ratio
    top_left_y: Ratio
    bottom_right_x: Ratio
    bottom_right_y: Ratio

    @model_validator(mode="after")
    def ordered_corners(self):
        if self.top_left_x >= self.bottom_right_x or self.top_left_y >= self.bottom_right_y:
            raise ValueError("Rectangle must have positive width and height")
        return self


class EraseSegment(NativeModel):
    start_time: float = Field(ge=0, description="Source time in seconds; does not trim the video.")
    end_time: float = Field(gt=0)
    erase_ratio_location: list[EraseLocation] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def ordered_times(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self


class TimeSegmentFilter(NativeModel):
    mode: Literal["selected", "skip"]
    segments: list[EraseSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def segment_regions(self):
        if self.mode == "skip" and any(segment.erase_ratio_location is not None for segment in self.segments):
            raise ValueError("Per-segment rectangles require time_segment_filter.mode=selected")
        return self


class SubtitleFilter(NativeModel):
    min_text_height_ratio: Ratio | None = None
    max_text_height_ratio: Ratio | None = None
    center_offset_ratio: Ratio | None = None


class SubtitleEraseRequest(NativeModel):
    video_url: str = Field(min_length=1, description="HTTP(S), mediakit://, vod:// or tos://. Upload local video with video_subtitle_erase_upload first.")
    model_version: Literal["v4", "v5"] = Field(default="v5", description="Wrapper defaults to v5 for improved flicker/shadow handling; official endpoint defaults to v4 if omitted.")
    mode: Literal["Subtitle", "Text"] = Field(default="Subtitle", description="Subtitle only detects captions in the lower half. Text also removes other overlay text; use only with explicit authorization, preferably scoped rectangles.")
    output_encode_mode: Literal["Quality", "Size"] = "Quality"
    erase_ratio_location: list[EraseLocation] | None = Field(default=None, min_length=1, max_length=20)
    time_segment_filter: TimeSegmentFilter | None = None
    subtitle_filter: SubtitleFilter | None = None
    media_output_destination: str | None = None
    client_token: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[\x20-\x7e]+$", description="Native idempotency token. Keep the same token after an uncertain submission. Never recreate to poll.")
    callback_args: str | None = None
    callback_url: str | None = None
    queue_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_native(self):
        if not valid_url(self.video_url, {"http", "https", "mediakit", "vod", "tos"}):
            raise ValueError("video_url must be a supported remote URI, not a local path or base64")
        if self.callback_url is not None and not valid_url(self.callback_url, {"http", "https"}):
            raise ValueError("Invalid callback_url")
        if self.media_output_destination is not None and not valid_url(self.media_output_destination, {"vod", "tos"}):
            raise ValueError("media_output_destination must be vod:// or tos://")
        if self.callback_args is not None and len(self.callback_args.encode("utf-8")) > 512:
            raise ValueError("callback_args exceeds 512 UTF-8 bytes")
        if self.time_segment_filter is not None and self.erase_ratio_location is not None:
            if any(segment.erase_ratio_location is not None for segment in self.time_segment_filter.segments):
                raise ValueError("Global and per-segment rectangles are mutually exclusive")
        if self.subtitle_filter is not None:
            if self.mode != "Subtitle":
                raise ValueError("subtitle_filter only applies to Subtitle mode")
            low = self.subtitle_filter.min_text_height_ratio
            high = self.subtitle_filter.max_text_height_ratio
            low = 0.01 if low is None else low
            high = (0.1 if self.model_version == "v5" else 0.2) if high is None else high
            if low > high:
                raise ValueError("Minimum text height exceeds the effective maximum for model_version")
        return self


def prepare_subtitle_erase(request: SubtitleEraseRequest) -> tuple[str, dict]:
    return ENDPOINT, request.model_dump(exclude_none=True)


def subtitle_erase_preview(request: SubtitleEraseRequest) -> dict:
    endpoint, body = prepare_subtitle_erase(request)
    for name in ("video_url", "callback_url", "callback_args", "media_output_destination", "client_token", "queue_id"):
        if name in body:
            body[name] = "[redacted]"
    warnings = [
        "Fine-erasure endpoint only. Wrapper explicitly selects v5 + Quality by default; no standard-version fallback.",
        "Provider accepts up to 2K input and outputs at most 1080p. Higher-resolution sources are not preserved at original resolution.",
        "Remote duration, resolution, account access and repair quality are validated by the provider; no local probe, upload or paid request occurs in preview.",
        "No translation, dubbing, trimming, muting, frame-rate change or enhancement is requested. Repair is not guaranteed pixel-identical or artifact-free.",
    ]
    spatially_scoped = bool(request.erase_ratio_location) or bool(request.time_segment_filter and
        all(segment.erase_ratio_location for segment in request.time_segment_filter.segments))
    if not spatially_scoped:
        warnings.append("No complete rectangle scope: inspect source frames and consider tight observed subtitle rectangles including outlines/shadows. Unspecified segments use the provider's default detection area.")
    if request.time_segment_filter is None:
        warnings.append("No time scope: the full video is submitted for erasure and billing. Use observed subtitle intervals when only part needs processing.")
    warnings.append("Before delivery, compare source/result texture, edges and temporal flicker as well as remaining subtitles/audio. Use video_subtitle_erase_qc for evidence; it never gives an automatic visual-quality pass. Keep the master and use video_export_publish for a separate smaller copy.")
    if request.mode == "Subtitle":
        warnings.append("Subtitle mode applies only to the lower 50% of the frame, intersected with any supplied rectangles. Rectangles do not override this restriction.")
    else:
        warnings.append("Text mode may erase other overlaid text, including names and titles. Use only with explicit authorization; scope rectangles to the intended captions.")
    if request.time_segment_filter is not None:
        warnings.append("Time filters control erasure only; they do not shorten the output video. The provider validates times against source duration.")
    return {"endpoint": endpoint, "body": body, "warnings": warnings, "paid_request_sent": False,
            "scope": {"rectangles_for_all_processed_segments": spatially_scoped,
                      "time_filter_supplied": request.time_segment_filter is not None}}


def subtitle_erase_capabilities() -> dict:
    return {
        "endpoint": ENDPOINT,
        "variant": "fine",
        "model_versions": ["v4", "v5"],
        "defaults": {"model_version": "v5", "mode": "Subtitle", "output_encode_mode": "Quality"},
        "provider_default_model_version": "v4",
        "limits": {"input_max_resolution": "2K", "output_max_resolution": "1080p", "max_rectangles": 20,
                   "task_history_days": 30, "temporary_result_url_hours": 24},
        "configured": bool(get_config("MEDIAKIT_API_KEY").strip()),
        "configuration": configuration_status(),
        "schema": SubtitleEraseRequest.model_json_schema(),
        "sources": SOURCES,
        "notes": [
            "Uses MEDIAKIT_API_KEY independently of ARK_API_KEY. No new credentials are required if MediaKit is already configured and authorized.",
            "Subtitle mode only processes the lower half. Text mode has broader semantics and must be explicitly selected.",
            "Global rectangles and per-segment rectangles are mutually exclusive; per-segment rectangles require selected mode.",
            "Upload transfers only the requested local video unchanged. Create submits once; get queries the same task; download saves exact output bytes to a new file.",
            "No automatic paid retry, model fallback, translation, dubbing, timeline edit or local re-encoding.",
            "v5 improves AIGC flicker, shadowed captions, false erasures and speed according to official documentation; no guarantee for a particular video.",
        ],
    }


class SubtitleEraseClient(MediaKitClient):
    async def create(self, request: SubtitleEraseRequest) -> dict:
        endpoint, body = prepare_subtitle_erase(request)
        data = await self._request("POST", endpoint, body)
        if not isinstance(data.get("task_id"), str) or not data["task_id"]:
            raise MediaKitError("MediaKit response has no task_id; submission outcome uncertain. Do not submit another task.")
        return {"variant": "fine", "model_version": request.model_version,
                "task": data, "paid_request_sent": True, "client_token": request.client_token,
                "next_step": "Query task.task_id with video_subtitle_erase_get_task. Do not recreate."}

    async def download_task(self, task_id: str, dest: str, *, max_bytes: int = 2 * 1024**3,
                            retries: int = 2, download_transport=None) -> dict:
        task = (await self.get(task_id))["task"]
        if task.get("status") != "completed":
            raise MediaKitError("Subtitle erasure task is not completed; query the same task ID later. No file downloaded.")
        result = task.get("result")
        url = result.get("video_url") if isinstance(result, dict) else None
        if not isinstance(url, str) or not valid_url(url, {"https"}):
            raise MediaKitError("Task has no HTTPS download URL. For vod:// or tos:// output, use the authorized storage service to retrieve the file.")
        return await download(url, dest, transport=download_transport, max_bytes=max_bytes, retries=retries)
