"""Native request fields for the official v5.0 song and BGM APIs."""
from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MUSIC_MODEL = "v5.0"
BillingMode = Literal["postpaid", "prepaid"]


class MusicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True, allow_inf_nan=False)


class MusicWatermark(MusicModel):
    ContentProducer: str | None = None
    ProduceId: str | None = None
    ContentPropagator: str | None = None
    PropagateId: str | None = None
    Enable: bool | None = None


class MusicOutputOptions(MusicModel):
    CallbackURL: str | None = None
    TosBucket: str | None = Field(default=None, min_length=1)
    ImplicitWaterMark: MusicWatermark | None = None
    AigcWatermark: bool | None = None

    @field_validator("CallbackURL")
    @classmethod
    def callback_url(cls, value):
        if value is not None:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                    or parsed.password or parsed.fragment or any(c.isspace() for c in value)):
                raise ValueError("CallbackURL requires an HTTP(S) URL without embedded credentials or fragments")
        return value


class SongRequest(MusicOutputOptions):
    ModelVersion: Literal["v5.0"] = MUSIC_MODEL
    Lyrics: str | None = Field(default=None, min_length=5, max_length=2000,
                              description="Custom lyrics, preserved verbatim; supports [verse], [chorus], etc.")
    Prompt: str | None = Field(default=None, min_length=5, max_length=2000,
                              description="Song description; v5.0 permits Lyrics and Prompt together. Old style fields are unsupported.")
    Duration: int | None = Field(default=None, ge=30, le=240,
                                description="Requested seconds; omit for provider-selected duration.")
    Lang: Literal["Chinese", "English", "Cantonese"] = "Chinese"
    VodFormat: Literal["wav", "mp3"] = "wav"
    SkipCopyCheck: bool = Field(default=False, description="Official copyright-check switch; false keeps checks enabled.")

    @model_validator(mode="after")
    def valid_text(self):
        if self.Lyrics is None and self.Prompt is None:
            raise ValueError("Provide Lyrics or Prompt (or both for v5.0)")
        for name in ("Lyrics", "Prompt"):
            value = getattr(self, name)
            if value is None:
                continue
            if len(value.strip()) < 5:
                raise ValueError(f"{name} requires at least five non-padding characters")
            # The provider documents 700 characters for Chinese, 2000 for English.
            chinese = bool(re.search(r"[\u3400-\u9fff]", value))
            limit = 700 if chinese or (name == "Lyrics" and self.Lang != "English") else 2000
            if len(value) > limit:
                raise ValueError(f"{name} exceeds the documented language length limit")
        return self


class BGMSegment(MusicModel):
    Name: Literal["intro", "verse", "chorus", "inst", "bridge", "outro"]
    Duration: int = Field(ge=5, le=120)


class BGMRequest(MusicOutputOptions):
    Version: Literal["v5.0"] = MUSIC_MODEL
    Text: str = Field(min_length=1, description="Chinese description of style, mood, scene and instruments.")
    Duration: int = Field(default=60, ge=30, le=120)
    EnableInputRewrite: bool = False
    Segments: list[BGMSegment] | None = Field(default=None, min_length=1, max_length=24)

    @model_validator(mode="after")
    def valid_bgm(self):
        if not re.search(r"[\u3400-\u9fff]", self.Text):
            raise ValueError("The BGM API requires a Chinese Text description")
        if self.Segments is not None and not 30 <= sum(s.Duration for s in self.Segments) <= 120:
            raise ValueError("Segments must total 30..120 seconds, including the single-segment case")
        return self
