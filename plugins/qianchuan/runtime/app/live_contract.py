"""Narrow SDK-derived live UNI contract; no production business validation implied."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

SDK_SOURCE = "https://github.com/oceanengine/ad_open_sdk_go/blob/master/models/model_qianchuan_uni_aweme_ad_create_v1_0_request.go"


class Creative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    smart_select_material: StrictBool
    hide_in_aweme: StrictBool | None = None


class Delivery(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    budget: float = Field(gt=0)
    smart_bid_type: Literal["SMART_BID_CONSERVATIVE", "SMART_BID_CUSTOM"]
    live_schedule_type: Literal["SCHEDULE_FROM_NOW"]
    roi2_goal: float | None = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def bid_roi(self):
        if self.smart_bid_type == "SMART_BID_CONSERVATIVE" and "roi2_goal" in self.model_fields_set:
            raise ValueError("Conservative bidding must omit roi2_goal, including null")
        if self.smart_bid_type == "SMART_BID_CUSTOM" and self.roi2_goal is None:
            raise ValueError("Custom bidding requires explicit roi2_goal")
        return self


class LiveCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    advertiser_id: int = Field(gt=0, strict=True)
    aweme_id: int = Field(gt=0, strict=True)
    name: str = Field(min_length=1, max_length=100)
    marketing_goal: Literal["LIVE_PROM_GOODS"]
    delivery_setting: Delivery
    creative_setting: Creative


def validate_live(payload):
    return LiveCreate.model_validate(payload).model_dump(exclude_none=True)


def contract():
    return {"status": "sdk_derived_not_live_tested", "schema": LiveCreate.model_json_schema(),
            "source": SDK_SOURCE, "endpoint": "/v1.0/qianchuan/uni_aweme/ad/create/",
            "live_tested": False, "write_gate": "QIANCHUAN_LIVE_CREATE_ENABLED=false by default",
            "supported_subset": "Immediate live UNI, explicit smart material selection; no scheduled/custom material variants yet",
            "limitations": ["SDK types do not prove conditional API business requirements", "No standard Campaign or SXT order", "Do not include product arrays or video_schedule_type"]}
