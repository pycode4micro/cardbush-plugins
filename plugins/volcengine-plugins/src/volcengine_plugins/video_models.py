"""Native Ark Seedance schema. Capability profiles are NOT interchangeable."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StrictBool, StrictInt

from .models import StrictModel

Profile = Literal["2.0", "2.0-fast", "2.0-mini", "2.5"]
DEFAULT_VIDEO_MODEL = "doubao-seedance-2-5-260628"
PROFILES = {
    "2.0": {"model": "doubao-seedance-2-0-260128", "resolutions": ["480p", "720p", "1080p", "4k"], "max_duration": 15, "images": 9, "videos": 3, "audios": 3, "total": 15},
    "2.0-fast": {"model": "doubao-seedance-2-0-fast-260128", "resolutions": ["480p", "720p"], "max_duration": 15, "images": 9, "videos": 3, "audios": 3, "total": 15},
    "2.0-mini": {"model": "doubao-seedance-2-0-mini-260615", "resolutions": ["480p", "720p"], "max_duration": 15, "images": 9, "videos": 3, "audios": 3, "total": 15},
    "2.5": {"model": DEFAULT_VIDEO_MODEL, "resolutions": ["480p", "720p", "1080p"], "max_duration": 30, "images": 30, "videos": 10, "audios": 10, "total": 50},
}
ALIASES = {**{key: value["model"] for key, value in PROFILES.items()}, "2.5-pro": DEFAULT_VIDEO_MODEL}


class MediaURL(StrictModel):
    url: str = Field(min_length=1, description="Native HTTPS URL, asset://ID, or image/audio base64 data URI. Local image/audio absolute paths require local.allow_local_files=true; local videos are not uploaded by this adapter.")


class VideoText(StrictModel):
    type: Literal["text"]
    text: str = Field(min_length=1, description="Native prompt, preserved verbatim. Refer to ordered media as 图片1 / 视频1 / 音频1. No automatic rewriting, muting, splitting or voice replacement.")


class VideoImage(StrictModel):
    type: Literal["image_url"]
    image_url: MediaURL
    role: Literal["first_frame", "last_frame", "reference_image"] | None = None


class VideoReference(StrictModel):
    type: Literal["video_url"]
    video_url: MediaURL
    role: Literal["reference_video"]


class AudioReference(StrictModel):
    type: Literal["audio_url"]
    audio_url: MediaURL
    role: Literal["reference_audio"]


VideoContent = Annotated[VideoText | VideoImage | VideoReference | AudioReference, Field(discriminator="type")]


class VideoTool(StrictModel):
    type: Literal["web_search"]


class VideoRequest(StrictModel):
    model: str | None = Field(default=None, min_length=1, description="Official model ID or aliases 2.0, 2.0-fast, 2.0-mini, 2.5, 2.5-pro. ep-* IDs require explicit local.capability_profile. Defaults to SEEDANCE_MODEL or official 2.5.")
    content: list[VideoContent] = Field(min_length=1, description="Ordered native multimodal content. First/last frames cannot mix with omni references. Media order and prompt are preserved.")
    resolution: Literal["480p", "720p", "1080p", "4k"] | None = None
    ratio: Literal["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"] | None = None
    duration: StrictInt | None = Field(default=None, description="Seconds: -1 (model decides), or 4..15 for 2.0 series / 4..30 for 2.5. Omission preserves official default. Never silently shorten or split.")
    generate_audio: StrictBool | None = Field(default=None, description="Native synchronized sound generation. Set true explicitly when audio is required; does not guarantee exact timbre or words.")
    watermark: StrictBool | None = None
    return_last_frame: StrictBool | None = None
    omni_reference_task_type: Literal["auto", "reference", "edit", "extend"] | None = Field(default=None, description="2.5 ONLY. edit requires reference video, ratio adaptive and duration -1; extend requires reference video and adaptive ratio. 2.0 edit/extend intent is expressed in the prompt, not this field.")
    output_format: Literal["mp4", "mov"] | None = Field(default=None, description="2.5 ONLY; other models must omit this field, including mp4.")
    callback_url: str | None = None
    execution_expires_after: Annotated[StrictInt, Field(ge=3600, le=259200)] | None = None
    priority: Annotated[StrictInt, Field(ge=0, le=9)] | None = None
    safety_identifier: str | None = Field(default=None, max_length=64, pattern=r"^[\x00-\x7f]*$")
    tools: list[VideoTool] | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict, description="Future official fields only, requiring local.allow_unverified_parameters=true. Cannot bypass known capability constraints or transport/auth settings.")


class VideoLocalOptions(StrictModel):
    capability_profile: Profile | None = Field(default=None, description="Required for ep-* or unrecognized model IDs; assertion of endpoint's actual underlying model, not model substitution.")
    allow_unverified_parameters: bool = False
    allow_local_files: bool = Field(default=False, description="Explicitly permit local image/audio paths in content URLs. Reads and validates bytes, embeds as base64 at the same index. Does not upload local videos or transform media.")
