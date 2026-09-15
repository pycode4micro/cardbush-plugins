"""Read-only live UNI adapters; no write helpers or token storage here."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

LiveTopic = Literal["OVERALL_ROI_LIVE_AWEME", "OVERALL_ROI_LIVE_MATERIAL_LIVE",
                    "OVERALL_ROI_LIVE_MATERIAL_VIDEO"]
TOPICS = ["OVERALL_ROI_LIVE_AWEME", "OVERALL_ROI_LIVE_MATERIAL_LIVE",
          "OVERALL_ROI_LIVE_MATERIAL_VIDEO"]


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1)
    operator: StrictInt
    values: list[str]


class LiveReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    advertiser_id: StrictInt = Field(gt=0)
    data_topic: LiveTopic
    dimensions: list[str] = Field(min_length=1)
    metrics: list[str] = Field(min_length=1)
    filters: list[Filter]
    start_time: str
    end_time: str
    order_by: list[dict[str, Any]]
    page: StrictInt = Field(default=1, ge=1)
    page_size: Literal[10, 20, 50, 100, 200] = 100

    @model_validator(mode="after")
    def check_window(self):
        start = datetime.strptime(self.start_time, "%Y-%m-%d %H:%M:%S")
        end = datetime.strptime(self.end_time, "%Y-%m-%d %H:%M:%S")
        if end < start or (end.date() - start.date()).days >= 31:
            raise ValueError("Use an ordered window of at most 31 calendar days (local query bound).")
        if any(not value.strip() for value in self.dimensions + self.metrics):
            raise ValueError("Empty dimensions/metrics are not allowed.")
        return self


def identity(advertiser_id: int, ad_id: int) -> dict[str, int]:
    if any(type(v) is not int or v <= 0 for v in (advertiser_id, ad_id)):
        raise ValueError("advertiser_id and ad_id must be positive integers")
    return {"advertiser_id": advertiser_id, "ad_id": ad_id}


def diagnose(service, advertiser_id: int, ad_id: int, page: int, page_size: int):
    params = identity(advertiser_id, ad_id)
    if type(page) is not int or page < 1 or page_size not in (10, 20, 50, 100):
        raise ValueError("Invalid diagnostic pagination")
    detail = service.get_uni_promotion_ad_detail(params)
    result = {"status": "blocked", "actions": [], "detail": detail,
              "advertiser_id": advertiser_id, "ad_id": ad_id,
              "suggestions": None, "complete": False}
    if detail.get("code") != 0:
        result["reason"] = "official_detail_error"
        return result
    data = detail.get("data") or {}
    if str(data.get("ad_id")) != str(ad_id) or data.get("marketing_goal") != "LIVE_PROM_GOODS":
        result["reason"] = "live_plan_identity_unverified"
        return result
    if data.get("advertiser_id") is not None and str(data["advertiser_id"]) != str(advertiser_id):
        result["reason"] = "advertiser_mismatch"
        return result
    suggestions = service.get_uni_promotion_ad_suggestions({**params, "page": page, "page_size": page_size})
    result.update(status="ok" if suggestions.get("code") == 0 else "partial",
                  suggestions=suggestions, reason=None,
                  note="Official audit suggestions, not a complete traffic diagnosis. One page only; retain pagination. No automatic changes.")
    return result
