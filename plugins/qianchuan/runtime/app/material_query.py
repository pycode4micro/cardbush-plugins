"""Typed contract for the official account-material query (no remote probing).

Source: https://github.com/oceanengine/ad_open_sdk_go/blob/master/api/api_qianchuan_material_get_v10.go
Filtering: https://github.com/oceanengine/ad_open_sdk_go/blob/master/models/model_qianchuan_material_get_v1_0_filtering.go
"""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MaterialFiltering(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_type: Literal["VIDEO", "IMAGE", "TITLE", "LIVE_ROOM"]
    start_time: str = Field(description="Start of query period, YYYY-MM-DD or YYYY-MM-DD HH:MM:SS (Asia/Shanghai). Belongs inside filtering.")
    end_time: str = Field(description="End of query period, same format as start_time. Belongs inside filtering.")
    search_keyword: str | None = None
    having_cost: Literal["ALL", "YES"] | None = None
    analysis_type: list[Literal["CARRY_MATERIAL", "FIRST_PUBLISH_MATERIAL", "HIGH_QUALITY_MATERIAL", "LOW_EFFICIENCY_MATERIAL", "POOR_QUALITY_MATERIAL", "SIMILAR_RISK_MATERIAL"]] | None = None
    image_mode: list[Literal["CAROUSEL", "LARGE", "LARGE_VERTICAL", "SMALL", "VIDEO_LARGE", "VIDEO_VERTICAL"]] | None = None
    video_source: list[Literal["ARTHUR", "AWEME", "BP", "CREATIVE_CENTER", "E_COMMERCE", "JIANYING", "JI_CHUANG", "LIVE_HIGHLIGHT", "QUNFENG", "STAR", "TADA", "VIDEO_CAPTURE"]] | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
            try:
                if datetime.strptime(value, fmt).strftime(fmt) == value:
                    return value
            except ValueError:
                pass
        raise ValueError("Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS inside filtering.")

    @model_validator(mode="after")
    def validate_range(self):
        if len(self.start_time) != len(self.end_time):
            raise ValueError("start_time and end_time must use the same date/time format.")
        if datetime.fromisoformat(self.start_time) > datetime.fromisoformat(self.end_time):
            raise ValueError("start_time must not be later than end_time.")
        return self


class MaterialQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advertiser_id: int = Field(gt=0)
    marketing_goal: Literal["LIVE_PROM_GOODS", "VIDEO_PROM_GOODS"]
    marketing_scene: Literal["FEED", "SEARCH", "SHOPPING_MALL"]
    filtering: MaterialFiltering
    fields: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1, description="Requested metric fields, e.g. stat_cost, show_cnt, click_cnt.")
    campaign_scene: list[Literal["DAILY_SALE", "LIVE_HEAT", "PLANT_GRASS"]] | None = None
    page: int = Field(default=1, ge=1)
    page_size: Literal[10, 20, 50, 100] = 20
    order_type: Literal["ASC", "DESC"] | None = None
    order_field: str | None = None
