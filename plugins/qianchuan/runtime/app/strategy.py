from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from app.qianchuan_client import QianchuanApiError, QianchuanClient
from app.schemas import (
    ActionProposal,
    AdRoiInsight,
    AdStatusUpdateRequest,
    BidUpdateRequest,
    BudgetUpdateItem,
    BudgetUpdateRequest,
    MaterialFatigueInsight,
    MaterialFatigueRequest,
    MaterialFatigueResponse,
    ReportQuery,
    RoiDiagnosisRequest,
    RoiDiagnosisResponse,
    RoiDiagnosisSummary,
    RoiGoalUpdateRequest,
    ToolCallRequest,
)
from app.tools import get_tool, list_tools

ACCOUNT_REPORT_PATH = "/v1.0/qianchuan/report/advertiser/get/"
AD_REPORT_PATH = "/v1.0/qianchuan/report/ad/get/"
MATERIAL_REPORT_PATH = "/v1.0/qianchuan/report/material/get/"
SEARCH_WORD_REPORT_PATH = "/v1.0/qianchuan/report/search_word/get/"
UNI_PROMOTION_REPORT_PATH = "/v1.0/qianchuan/report/uni_promotion/data/get/"
SUGGEST_ROI_GOAL_PATH = "/v1.0/qianchuan/suggest/roi/goal/"
SUGGEST_BUDGET_PATH = "/v1.0/qianchuan/suggest/budget/"
ESTIMATE_EFFECT_PATH = "/v1.0/qianchuan/estimate/effect/"
BUDGET_UPDATE_PATH = "/v1.0/qianchuan/ad/budget/update/"
BID_UPDATE_PATH = "/v1.0/qianchuan/ad/bid/update/"
ROI_GOAL_UPDATE_PATH = "/v1.0/qianchuan/roi/goal/update/"
AD_STATUS_UPDATE_PATH = "/v1.0/qianchuan/ad/status/update/"
AUTHORIZED_ADVERTISERS_PATH = "/oauth2/advertiser/get/"

ROI_KEYS = (
    "all_order_pay_roi_7days",
    "prepay_and_pay_order_roi",
    "create_order_roi",
    "pay_order_roi",
    "roi",
)
COST_KEYS = ("stat_cost", "cost", "consume")
ORDER_KEYS = ("pay_order_count", "create_order_count", "order_count")
CLICK_KEYS = ("click_cnt", "click_count", "click")
PAY_AMOUNT_KEYS = ("pay_order_amount", "total_pay_order_amount", "order_pay_amount")
CONVERSION_COST_KEYS = ("convert_cost", "ecp_cpa_platform", "conversion_cost")
MATERIAL_ID_KEYS = ("material_id", "creative_id", "video_id", "image_id", "id")
MATERIAL_NAME_KEYS = ("material_name", "creative_name", "video_name", "image_name", "name")
SHOW_KEYS = ("show_cnt", "show_count", "impression")


class StrategyError(ValueError):
    pass


class WriteDisabledError(StrategyError):
    pass


class ConfirmationRequiredError(StrategyError):
    pass


class ValidationError(StrategyError):
    pass


class QianchuanStrategyService:
    def __init__(
        self,
        *,
        client: QianchuanClient,
        access_token: str,
        write_enabled: bool,
        default_target_roi: Decimal,
        default_min_spend: Decimal,
        default_min_clicks: int,
        default_min_orders: int,
    ) -> None:
        self.client = client
        self.access_token = access_token
        self.write_enabled = write_enabled
        self.default_target_roi = default_target_roi
        self.default_min_spend = default_min_spend
        self.default_min_clicks = default_min_clicks
        self.default_min_orders = default_min_orders

    def list_tools(self) -> list[dict[str, object]]:
        return list_tools()

    def call_tool(self, key: str, request: ToolCallRequest) -> dict[str, Any]:
        tool = get_tool(key)
        if tool is None:
            raise ValidationError(f"Unknown Qianchuan tool key: {key}")
        if tool.write:
            self._ensure_write_allowed(confirm=request.confirm, reason=request.reason)
        if tool.method == "GET":
            return self.client.get(tool.path, access_token=self.access_token, params=request.params)
        return self.client.post(tool.path, access_token=self.access_token, json_body=request.payload)

    def get_authorized_accounts(self) -> dict[str, Any]:
        return self.client.get(AUTHORIZED_ADVERTISERS_PATH, access_token=self.access_token)

    def get_available_products(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_product_available_get_v1", ToolCallRequest(params=params)
        )

    def get_aweme_available_products(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_aweme_product_available_get_v1", ToolCallRequest(params=params)
        )

    def get_authorized_aweme_accounts(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_aweme_authorized_get_v1", ToolCallRequest(params=params)
        )

    def get_aweme_available_videos(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return shop videos for Aweme-order/SXT workflows, not PC uni-promotion."""
        return self.call_tool(
            "qianchuan_aweme_video_get_v1", ToolCallRequest(params=params)
        )

    def get_aweme_existing_videos(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return existing videos under an Aweme account, optionally filtered by product."""
        return self.call_tool(
            "qianchuan_file_video_aweme_get_v1", ToolCallRequest(params=params)
        )

    def get_carousels(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return Qianchuan carousel (图文) materials available to an advertiser."""
        return self.call_tool("qianchuan_carousel_get_v1", ToolCallRequest(params=params))

    def get_aweme_carousels(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return published Aweme carousel (图文) materials for a permitted account."""
        return self.call_tool(
            "qianchuan_carousel_aweme_get_v1", ToolCallRequest(params=params)
        )

    def get_uni_aweme_carousel_candidates(
        self,
        *,
        advertiser_id: int,
        ad_id: int,
        aweme_id: int,
        product_id: int,
        marketing_goal: str = "VIDEO_PROM_GOODS",
        cursor: int = 0,
        count: int = 50,
    ) -> dict[str, Any]:
        """List published product-specific Aweme carousels after plan identity checks."""
        if min(advertiser_id, ad_id, aweme_id, product_id) < 1:
            raise ValidationError("advertiser_id, ad_id, aweme_id, and product_id must be positive.")
        if cursor < 0:
            raise ValidationError("cursor must be at least 0.")
        if count < 1 or count > 100:
            raise ValidationError("count must be between 1 and 100.")
        if marketing_goal != "VIDEO_PROM_GOODS":
            raise ValidationError(
                "This guarded candidate query currently supports VIDEO_PROM_GOODS only."
            )
        detail = self.get_uni_promotion_ad_detail(
            {"advertiser_id": advertiser_id, "ad_id": ad_id}
        )
        detail_data = detail.get("data") if isinstance(detail, dict) else None
        if not isinstance(detail_data, dict) or str(detail_data.get("ad_id")) != str(ad_id):
            raise ValidationError("Official detail did not return the requested uni-promotion plan.")
        if str(detail_data.get("aweme_id")) != str(aweme_id):
            raise ValidationError("The requested Aweme account does not match official plan detail.")
        if str(detail_data.get("marketing_goal")) != marketing_goal:
            raise ValidationError("The requested marketing goal does not match official plan detail.")
        if str(product_id) not in _extract_plan_product_ids(detail_data):
            raise ValidationError("The requested product is not bound to the official plan detail.")

        response = self.get_aweme_carousels(
            {
                "advertiser_id": advertiser_id,
                "aweme_id": aweme_id,
                "filtering": {"product_id": product_id},
                "cursor": cursor,
                "count": count,
            }
        )
        data = response.get("data") if isinstance(response, dict) else None
        rows = data.get("image_aggregate_list") if isinstance(data, dict) else None
        candidates = [item for item in (rows or []) if isinstance(item, dict)]
        return {
            "status": "ok",
            "read_only": True,
            "plan_verified": {
                "advertiser_id": advertiser_id,
                "ad_id": ad_id,
                "aweme_id": aweme_id,
                "product_id": product_id,
                "marketing_goal": marketing_goal,
                "opt_status": detail_data.get("opt_status"),
                "status": detail_data.get("status"),
            },
            "query": {"filtering": {"product_id": product_id}, "cursor": cursor, "count": count},
            "candidates": candidates,
            "count": len(candidates),
            "page_info": data.get("page_info") if isinstance(data, dict) else None,
            "request_ids": {
                "plan_detail": detail.get("request_id"),
                "published_product_carousels": response.get("request_id"),
            },
        }

    def get_uni_aweme_video_candidates(
        self,
        *,
        advertiser_id: int,
        ad_id: int,
        aweme_id: int,
        product_id: int,
        marketing_goal: str = "VIDEO_PROM_GOODS",
        cursor: int = 0,
        count: int = 50,
    ) -> dict[str, Any]:
        """Verify a uni-promotion plan and group its read-only Aweme video candidates.

        The official full-domain exclusion pool is scoped by advertiser, Aweme account,
        marketing goal, and product. Official plan detail is read first so an agent cannot
        accidentally use candidates from another identity or plan.
        """
        if min(advertiser_id, ad_id, aweme_id, product_id) < 1:
            raise ValidationError("advertiser_id, ad_id, aweme_id, and product_id must be positive.")
        if cursor < 0:
            raise ValidationError("cursor must be at least 0.")
        if count < 1 or count > 100:
            raise ValidationError("count must be between 1 and 100.")
        if marketing_goal != "VIDEO_PROM_GOODS":
            raise ValidationError(
                "This guarded candidate query currently supports VIDEO_PROM_GOODS only."
            )

        detail = self.get_uni_promotion_ad_detail(
            {"advertiser_id": advertiser_id, "ad_id": ad_id}
        )
        detail_data = detail.get("data") if isinstance(detail, dict) else None
        if not isinstance(detail_data, dict) or str(detail_data.get("ad_id")) != str(ad_id):
            raise ValidationError("Official detail did not return the requested uni-promotion plan.")
        if str(detail_data.get("aweme_id")) != str(aweme_id):
            raise ValidationError("The requested Aweme account does not match official plan detail.")
        if str(detail_data.get("marketing_goal")) != marketing_goal:
            raise ValidationError("The requested marketing goal does not match official plan detail.")

        plan_product_ids = _extract_plan_product_ids(detail_data)
        if str(product_id) not in plan_product_ids:
            raise ValidationError("The requested product is not bound to the official plan detail.")

        published_response: dict[str, Any] | None = None
        published_permission_error: dict[str, Any] | None = None
        try:
            published_response = self.get_aweme_existing_videos(
                {
                    "advertiser_id": advertiser_id,
                    "aweme_id": aweme_id,
                    "filtering": {"product_id": product_id},
                    "cursor": cursor,
                    "count": count,
                }
            )
        except QianchuanApiError as exc:
            if str(exc.code) != "40002":
                raise
            published_permission_error = {
                "code": exc.code,
                "message": str(exc),
                "request_id": exc.request_id,
                "endpoint": "/v1.0/qianchuan/file/video/aweme/get/",
            }

        pool_response = self.get_uni_promotion_block_materials(
            {
                "advertiser_id": advertiser_id,
                "aweme_id": aweme_id,
                "marketing_goal": marketing_goal,
                "media_type": "VIDEO",
                "product_id": [product_id],
                "cursor": cursor,
            }
        )
        published_data = (
            published_response.get("data") if isinstance(published_response, dict) else None
        )
        published_list = (
            published_data.get("video_list") if isinstance(published_data, dict) else None
        )
        published_rows = [item for item in (published_list or []) if isinstance(item, dict)]
        account_video_diagnostic: dict[str, Any] | None = None
        account_video_request_id: str | None = None
        if not published_rows and published_permission_error is None:
            account_response = self.get_aweme_existing_videos(
                {
                    "advertiser_id": advertiser_id,
                    "aweme_id": aweme_id,
                    "cursor": 0,
                    "count": count,
                }
            )
            account_data = account_response.get("data") if isinstance(account_response, dict) else None
            account_list = account_data.get("video_list") if isinstance(account_data, dict) else None
            account_rows = [item for item in (account_list or []) if isinstance(item, dict)]
            account_video_diagnostic = {
                "read_only": True,
                "count": len(account_rows),
                "page_info": (
                    account_data.get("page_info") if isinstance(account_data, dict) else None
                ),
                "review_only_items": [_video_review_summary(item) for item in account_rows],
                "warning": (
                    "These videos are visible under the Aweme account but were not returned by "
                    "the official product_id filter. Do not bind them as product candidates."
                ),
            }
            account_video_request_id = account_response.get("request_id")
        pool_data = pool_response.get("data") if isinstance(pool_response, dict) else None
        pool_list = pool_data.get("video_list") if isinstance(pool_data, dict) else None
        pool_rows = [item for item in (pool_list or []) if isinstance(item, dict)]

        exact_product_candidates: list[dict[str, Any]] = []
        unlinked_candidates: list[dict[str, Any]] = []
        other_product_videos: list[dict[str, Any]] = []
        for item in pool_rows:
            product_info = item.get("product_info")
            candidate_product_id = item.get("product_id")
            if isinstance(product_info, dict):
                candidate_product_id = (
                    candidate_product_id
                    or product_info.get("id")
                    or product_info.get("product_id")
                )
            if candidate_product_id in (None, "", 0, "0"):
                unlinked_candidates.append(item)
            elif str(candidate_product_id) == str(product_id):
                exact_product_candidates.append(item)
            else:
                other_product_videos.append(item)

        return {
            "status": "ok",
            "read_only": True,
            "plan_verified": {
                "advertiser_id": advertiser_id,
                "ad_id": ad_id,
                "aweme_id": aweme_id,
                "product_id": product_id,
                "marketing_goal": marketing_goal,
                "opt_status": detail_data.get("opt_status"),
                "status": detail_data.get("status"),
            },
            "query": {
                "cursor": cursor,
                "count": count,
                "media_type": "VIDEO",
                "product_id": [product_id],
            },
            "published_product_candidates": published_rows,
            "exact_product_candidates": published_rows,
            "full_domain_exclusion_pool": {
                "exact_product": exact_product_candidates,
                "unlinked": unlinked_candidates,
                "other_product": other_product_videos,
            },
            "unlinked_candidates": unlinked_candidates,
            "other_product_videos": other_product_videos,
            "counts": {
                "published_product": len(published_rows),
                "full_domain_pool": len(pool_rows),
                "pool_exact_product": len(exact_product_candidates),
                "pool_unlinked": len(unlinked_candidates),
                "pool_other_product": len(other_product_videos),
            },
            "page_info": {
                "published_product": (
                    published_data.get("page_info") if isinstance(published_data, dict) else None
                ),
                "full_domain_pool": (
                    pool_data.get("page_info") if isinstance(pool_data, dict) else None
                ),
            },
            "request_ids": {
                "plan_detail": detail.get("request_id"),
                "published_product_videos": (
                    published_response.get("request_id")
                    if isinstance(published_response, dict)
                    else None
                ),
                "unfiltered_account_videos": account_video_request_id,
                "full_domain_video_pool": pool_response.get("request_id"),
            },
            "permission_error": published_permission_error,
            "account_video_diagnostic": account_video_diagnostic,
            "root_cause_hint": _video_candidate_root_cause(
                published_rows=published_rows,
                permission_error=published_permission_error,
                account_video_diagnostic=account_video_diagnostic,
            ),
            "source_endpoints": {
                "published_product_videos": "/v1.0/qianchuan/file/video/aweme/get/",
                "full_domain_pool": "/v1.0/qianchuan/uni_promotion/block_material/get/",
            },
            "selection_rule": (
                "Only published_product_candidates are direct product-filtered video candidates. "
                "The exclusion pool is supporting evidence, not proof of explicit bindability. "
                "Never bind or exclude anything without separate explicit authorization."
            ),
        }

    def get_uni_authorized_aweme_accounts(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_aweme_authorized_get_v1", ToolCallRequest(params=params)
        )

    def get_uni_aweme_suggested_budget(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_aweme_suggest_budget_v1", ToolCallRequest(params=params)
        )

    def get_uni_aweme_suggested_roi(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_aweme_suggest_roi_v1", ToolCallRequest(params=params)
        )

    def get_uni_authorizable_shops(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_authorizable_shop_list_v1",
            ToolCallRequest(params=params),
        )

    def apply_uni_promotion_authorization(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_uni_promotion_authorization_apply_v1", request)

    def initialize_uni_promotion_authorization(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_uni_promotion_auth_init_v1", request)

    def get_account_balance(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_account_balance_get_v1", ToolCallRequest(params=params)
        )

    def get_ad_quota(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool("qianchuan_ad_quota_get_v1", ToolCallRequest(params=params))

    def get_ad_reject_reasons(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_ad_reject_reason_v1", ToolCallRequest(params=params)
        )

    def get_ad_learning_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_ad_learning_status_get_v1", ToolCallRequest(params=params)
        )

    def get_account_report(self, query: ReportQuery) -> dict[str, Any]:
        return self.client.get(ACCOUNT_REPORT_PATH, access_token=self.access_token, params=self._report_params(query))

    def get_ad_report(self, query: ReportQuery) -> dict[str, Any]:
        return self.client.get(AD_REPORT_PATH, access_token=self.access_token, params=self._report_params(query))

    def get_material_report(self, query: ReportQuery) -> dict[str, Any]:
        return self.client.get(MATERIAL_REPORT_PATH, access_token=self.access_token, params=self._report_params(query))

    def get_search_word_report(self, query: ReportQuery) -> dict[str, Any]:
        return self.client.get(SEARCH_WORD_REPORT_PATH, access_token=self.access_token, params=self._report_params(query))

    def check_keywords(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_keyword_check_v1",
            ToolCallRequest(payload=payload),
        )

    def get_uni_promotion_report(self, query: ReportQuery) -> dict[str, Any]:
        return self.client.get(UNI_PROMOTION_REPORT_PATH, access_token=self.access_token, params=self._report_params(query))

    def get_uni_promotion_report_config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the current dimensions and metrics for one or more data topics."""
        return self.call_tool(
            "qianchuan_report_uni_promotion_config_get_v1",
            ToolCallRequest(params=params),
        )

    def get_uni_promotion_report_data(self, params: dict[str, Any]) -> dict[str, Any]:
        """Call the current topic-based full-domain report endpoint."""
        return self.call_tool(
            "qianchuan_report_uni_promotion_data_get_v1",
            ToolCallRequest(params=params),
        )

    def suggest_roi_goal(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.client.get(SUGGEST_ROI_GOAL_PATH, access_token=self.access_token, params=params)

    def suggest_budget(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.client.get(SUGGEST_BUDGET_PATH, access_token=self.access_token, params=params)

    def estimate_effect(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.client.get(ESTIMATE_EFFECT_PATH, access_token=self.access_token, params=params)

    def create_campaign(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_campaign_create_v1", request)

    def update_campaign(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_campaign_update_v1", request)

    def get_campaigns(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool("qianchuan_campaign_list_get_v1", ToolCallRequest(params=params))

    def create_ad(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_ad_create_v1", request)

    def update_ad(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_ad_update_v1", request)

    def update_account_budget(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_account_budget_update_v1", request)

    def update_ad_overall_marketing(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_ad_overall_marketing_update_v1", request)

    def upgrade_uni_promotion_to_multiplier(self, request: ToolCallRequest) -> dict[str, Any]:
        """Upgrade exactly one paused PC uni-promotion plan through the official endpoint."""
        payload = request.payload
        advertiser_id = payload.get("advertiser_id")
        upgrades = payload.get("ad_id_list")
        if not isinstance(advertiser_id, int) or advertiser_id <= 0:
            raise ValidationError("Multiplier upgrade requires a positive integer advertiser_id.")
        if not isinstance(upgrades, list) or len(upgrades) != 1 or not isinstance(upgrades[0], dict):
            raise ValidationError("Guarded multiplier upgrade requires exactly one ad_id_list entry.")

        upgrade = upgrades[0]
        ad_id = upgrade.get("ad_id")
        roi2_goal = upgrade.get("roi2_goal")
        if not isinstance(ad_id, int) or ad_id <= 0:
            raise ValidationError("Multiplier upgrade requires a positive integer ad_id.")
        if isinstance(roi2_goal, bool) or not isinstance(roi2_goal, (int, float)):
            raise ValidationError("Multiplier upgrade requires numeric roi2_goal.")
        if not 0 < float(roi2_goal) <= 100:
            raise ValidationError("Multiplier upgrade roi2_goal must be greater than 0 and at most 100.")

        detail = self.get_uni_promotion_ad_detail(
            {"advertiser_id": advertiser_id, "ad_id": ad_id}
        )
        detail_data = detail.get("data") if isinstance(detail, dict) else None
        if not isinstance(detail_data, dict) or str(detail_data.get("ad_id")) != str(ad_id):
            raise ValidationError(
                "Official detail did not return the exact full-domain plan selected for multiplier upgrade."
            )
        status_values = {
            str(detail_data.get("status") or "").upper(),
            str(detail_data.get("opt_status") or "").upper(),
        }
        if "DISABLE" not in status_values:
            raise ValidationError(
                "Multiplier upgrade requires the official full-domain plan to be paused (DISABLE)."
            )

        return self.call_tool("qianchuan_uni_promotion_multiplier_upgrade_v1", request)

    def update_ad_region(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_ad_region_update_v1", request)

    def update_ad_schedule_date(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_ad_schedule_date_update_v1", request)

    def update_ad_schedule_fixed_range(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_ad_schedule_fixed_range_update_v1", request)

    def update_campaign_status_batch(self, request: ToolCallRequest) -> dict[str, Any]:
        operation = str(
            request.payload.get("opt_status") or request.payload.get("operation") or ""
        ).upper()
        if operation not in {"ENABLE", "DISABLE"}:
            raise ValidationError(
                "The guarded campaign status tool only permits ENABLE or DISABLE."
            )
        return self.call_tool("qianchuan_batch_campaign_status_update_v1", request)

    def get_ads(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool("qianchuan_ad_get_v1", ToolCallRequest(params=params))

    def get_ad_detail(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool("qianchuan_ad_detail_get_v1", ToolCallRequest(params=params))

    def get_ad_materials(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool("qianchuan_ad_material_get_v1", ToolCallRequest(params=params))

    def delete_ad_material(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_ad_material_delete_v1", request)

    def get_materials(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool("qianchuan_material_get_v1", ToolCallRequest(params=params))

    def get_material_ads(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool("qianchuan_material_ad_get_v1", ToolCallRequest(params=params))

    def get_images(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool("qianchuan_image_get_v1", ToolCallRequest(params=params))

    def delete_images(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_image_delete_v1", request)

    def get_videos(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool("qianchuan_video_get_v1", ToolCallRequest(params=params))

    def upload_video(
        self,
        *,
        advertiser_id: int,
        file_path: Path,
        filename: str,
        video_signature: str,
        content_type: str,
        is_aigc: bool,
        labels: list[str],
        confirm: bool,
        reason: str | None,
    ) -> dict[str, Any]:
        self._ensure_write_allowed(confirm=confirm, reason=reason)
        return self.client.upload_video(
            "/2/file/video/ad/",
            access_token=self.access_token,
            advertiser_id=advertiser_id,
            file_path=file_path,
            filename=filename,
            video_signature=video_signature,
            content_type=content_type,
            is_aigc=is_aigc,
            labels=labels,
        )

    def upload_image(
        self,
        *,
        advertiser_id: int,
        file_path: Path,
        filename: str,
        image_signature: str,
        content_type: str,
        is_aigc: bool,
        confirm: bool,
        reason: str | None,
    ) -> dict[str, Any]:
        self._ensure_write_allowed(confirm=confirm, reason=reason)
        return self.client.upload_image(
            "/2/file/image/ad/",
            access_token=self.access_token,
            advertiser_id=advertiser_id,
            file_path=file_path,
            filename=filename,
            image_signature=image_signature,
            content_type=content_type,
            is_aigc=is_aigc,
        )

    def create_carousel_material(self, request: ToolCallRequest) -> dict[str, Any]:
        """Create a 图文素材库 entry from existing image-library IDs only."""
        return self.call_tool("qianchuan_carousel_create_v2", request)

    def create_uni_aweme_ad(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_uni_aweme_ad_create_v1", request)

    def update_uni_aweme_ad(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_uni_aweme_ad_update_v1", request)

    def add_aweme_order_budget(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_aweme_order_budget_add_v1", request)

    def add_aweme_uni_promotion_order_budget(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_aweme_uni_promotion_order_budget_add_v1", request)

    def set_smart_boost(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_tools_smart_boost_ad_boost_set_v1", request)

    def get_uni_promotions(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_list_v1",
            ToolCallRequest(params=params),
        )

    def get_uni_promotion_ad_detail(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_ad_detail_v1",
            ToolCallRequest(params=params),
        )

    def get_uni_promotion_products(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_product_get_v1",
            ToolCallRequest(params=params),
        )

    def update_uni_promotion_ad_budget(self, request: ToolCallRequest) -> dict[str, Any]:
        payload = dict(request.payload)
        # previous_budget is a local safety input used by account-policy rate checks.
        # The official endpoint only accepts update_budget_infos entries.
        update_infos = payload.get("update_budget_infos")
        if isinstance(update_infos, list):
            payload["update_budget_infos"] = [
                {key: value for key, value in item.items() if key != "previous_budget"}
                if isinstance(item, dict)
                else item
                for item in update_infos
            ]
        official_request = request.model_copy(update={"payload": payload})
        return self.call_tool("qianchuan_uni_promotion_ad_budget_update_v1", official_request)

    def update_uni_promotion_ad_roi2_goal(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_uni_promotion_ad_roi2_goal_update_v1", request)

    def update_uni_promotion_ad_status(self, request: ToolCallRequest) -> dict[str, Any]:
        payload = dict(request.payload)
        opt_status = str(payload.get("opt_status", "")).upper()
        if opt_status not in {"ENABLE", "DISABLE"}:
            raise ValidationError(
                "The guarded uni-promotion status tool only permits ENABLE or DISABLE; "
                "DELETE is intentionally blocked."
            )

        try:
            advertiser_id = int(payload["advertiser_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("Status payload must include a positive advertiser_id.") from exc
        if advertiser_id <= 0:
            raise ValidationError("Status payload must include a positive advertiser_id.")

        raw_ad_ids = payload.get("ad_ids")
        if not isinstance(raw_ad_ids, list) or not raw_ad_ids:
            raise ValidationError("Status payload must include a non-empty ad_ids list.")

        ad_ids: list[int] = []
        for raw_ad_id in raw_ad_ids:
            try:
                ad_id = int(raw_ad_id)
            except (TypeError, ValueError) as exc:
                raise ValidationError("Each status ad_id must be a positive integer.") from exc
            if ad_id <= 0:
                raise ValidationError("Each status ad_id must be a positive integer.")
            if ad_id not in ad_ids:
                ad_ids.append(ad_id)

        # A status update is safety-sensitive.  Read each plan first and fail closed
        # if the official API does not expose a usable opt_status.  This prevents a
        # monitor from repeatedly sending terminal DISABLE calls after a run ended.
        write_ad_ids: list[int] = []
        skipped_ad_ids: list[int] = []
        observed_statuses: dict[str, str] = {}
        for ad_id in ad_ids:
            detail = self.get_uni_promotion_ad_detail(
                {"advertiser_id": advertiser_id, "ad_id": ad_id}
            )
            data = detail.get("data")
            if not isinstance(data, dict):
                raise ValidationError(
                    f"Official detail for ad_id {ad_id} did not return data; "
                    "status write was not submitted."
                )
            observed_status = str(data.get("opt_status") or data.get("status") or "").upper()
            if observed_status not in {"ENABLE", "DISABLE"}:
                raise ValidationError(
                    f"Official detail for ad_id {ad_id} returned ambiguous status "
                    f"{observed_status or '<empty>'}; status write was not submitted."
                )
            observed_statuses[str(ad_id)] = observed_status
            if observed_status == opt_status:
                skipped_ad_ids.append(ad_id)
            else:
                write_ad_ids.append(ad_id)

        guard = {
            "requested_opt_status": opt_status,
            "requested_ad_ids": ad_ids,
            "written_ad_ids": write_ad_ids,
            "skipped_ad_ids": skipped_ad_ids,
            "observed_statuses": observed_statuses,
            "terminal": opt_status == "DISABLE",
        }
        if not write_ad_ids:
            return {
                "code": 0,
                "message": "All plans already have the requested status; official write skipped.",
                "data": {"official_write_skipped": True, **guard},
            }

        payload["ad_ids"] = write_ad_ids
        official_request = request.model_copy(update={"payload": payload})
        response = self.call_tool("qianchuan_uni_promotion_ad_status_update_v1", official_request)
        response["local_guard"] = guard
        return response

    def update_uni_promotion_ad_name(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_uni_promotion_ad_name_update_v1", request)

    def update_uni_promotion_ad_schedule(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_uni_promotion_ad_schedule_date_update_v1", request)

    def get_uni_promotion_ad_suggestions(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_ad_suggestion_v1", ToolCallRequest(params=params)
        )

    def get_product_competition_analyses(self, params: dict[str, Any]) -> dict[str, Any]:
        """List products available in the official competition-analysis dataset."""
        return self._call_product_competition_tool(
            "qianchuan_product_analyse_list_v1", params
        )

    def get_product_competition_stats_comparison(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Read the official performance comparison for selected products."""
        return self._call_product_competition_tool(
            "qianchuan_product_analyse_compare_stats_data_v1", params
        )

    def get_product_competition_creative_comparison(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Read the official creative comparison for selected products."""
        return self._call_product_competition_tool(
            "qianchuan_product_analyse_compare_creative_v1", params
        )

    def _call_product_competition_tool(
        self, key: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return self.call_tool(key, ToolCallRequest(params=params))
        except QianchuanApiError as exc:
            if exc.status_code == 404:
                raise ValidationError(
                    "official_endpoint_unavailable: the legacy Qianchuan product "
                    "competition endpoint is still listed in the official catalog but "
                    "the production gateway currently returns HTTP 404. Do not retry; "
                    "use current uni-promotion topic reports for local product, plan, "
                    "and creative performance instead."
                ) from exc
            raise

    def delete_uni_promotion_ad(self, request: ToolCallRequest) -> dict[str, Any]:
        """Delete exactly one paused plan after matching its current official name."""
        payload = request.payload
        ad_ids = payload.get("ad_ids")
        expected_name = payload.get("expected_name")
        if (
            str(payload.get("opt_status", "")).upper() != "DELETE"
            or not isinstance(ad_ids, list)
            or len(ad_ids) != 1
            or ad_ids[0] in (None, "")
            or not isinstance(expected_name, str)
            or not expected_name.strip()
        ):
            raise ValidationError(
                "Guarded deletion requires one ad_id, opt_status=DELETE, and expected_name."
            )
        advertiser_id = payload.get("advertiser_id")
        ad_id = int(ad_ids[0])
        detail = self.get_uni_promotion_ad_detail(
            {"advertiser_id": advertiser_id, "ad_id": ad_id}
        )
        data = detail.get("data") if isinstance(detail, dict) else None
        if not isinstance(data, dict) or str(data.get("ad_id")) != str(ad_id):
            raise ValidationError("Official detail did not return the exact plan selected for deletion.")
        if str(data.get("name") or "") != expected_name:
            raise ValidationError("Official plan name does not match expected_name; deletion blocked.")
        status_values = {
            str(data.get("status") or "").upper(),
            str(data.get("opt_status") or "").upper(),
        }
        if "DISABLE" not in status_values:
            raise ValidationError("Guarded deletion requires the official plan to be paused first.")
        official_request = request.model_copy(
            update={
                "payload": {
                    "advertiser_id": advertiser_id,
                    "ad_ids": [ad_id],
                    "opt_status": "DELETE",
                }
            }
        )
        return self.call_tool(
            "qianchuan_uni_promotion_ad_status_update_v1", official_request
        )

    def create_aweme_order(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_aweme_order_create_v1", request)

    def create_aweme_uni_promotion_order(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_aweme_uni_promotion_order_create_v1", request)

    def get_aweme_uni_promotion_ad_materials(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_aweme_uni_promotion_ad_material_get_v1",
            ToolCallRequest(params=params),
        )

    def create_uni_promotion_control_task(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_uni_promotion_ad_control_task_create_v1", request)

    def create_uni_promotion_smart_control_task(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_ad_control_task_smart_control_create_v1",
            request,
        )

    def get_uni_promotion_control_tasks(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_ad_control_task_list_v1",
            ToolCallRequest(params=params),
        )

    def update_uni_promotion_control_task(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_uni_promotion_ad_control_task_update_v1", request)

    def update_uni_promotion_control_task_status(
        self, request: ToolCallRequest
    ) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_ad_control_task_status_update_v1", request
        )

    def update_uni_promotion_control_task_budget(
        self, request: ToolCallRequest
    ) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_ad_control_task_budget_update_v1", request
        )

    def update_uni_promotion_control_task_duration(
        self, request: ToolCallRequest
    ) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_ad_control_task_duration_update_v1", request
        )

    def update_uni_promotion_smart_control_task_status(
        self, request: ToolCallRequest
    ) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_ad_control_task_smart_control_status_update_v1",
            request,
        )

    def add_uni_promotion_ad_material(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_uni_promotion_ad_material_add_v1", request)

    def delete_uni_promotion_ad_material(self, request: ToolCallRequest) -> dict[str, Any]:
        return self.call_tool("qianchuan_uni_promotion_ad_material_delete_v1", request)

    def get_uni_promotion_ad_materials(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_ad_material_get_v1",
            ToolCallRequest(params=params),
        )

    def get_uni_promotion_ad_products(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_ad_product_get_v1",
            ToolCallRequest(params=params),
        )

    def get_uni_promotion_product_aweme_accounts(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_product_aweme_get_v1",
            ToolCallRequest(params=params),
        )

    def get_uni_promotion_block_materials(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.call_tool(
            "qianchuan_uni_promotion_block_material_get_v1",
            ToolCallRequest(params=params),
        )

    def analyze_material_fatigue(self, request: MaterialFatigueRequest) -> MaterialFatigueResponse:
        current_query = _material_query(
            request,
            start_date=request.current_start_date,
            end_date=request.current_end_date,
        )
        previous_query = _material_query(
            request,
            start_date=request.previous_start_date,
            end_date=request.previous_end_date,
        )
        current_payload = self.get_material_report(current_query)
        previous_payload = self.get_material_report(previous_query)
        return analyze_material_fatigue_from_rows(
            current_rows=_extract_rows(current_payload),
            previous_rows=_extract_rows(previous_payload),
            request=request,
            source={
                "endpoint": MATERIAL_REPORT_PATH,
                "current_request_id": current_payload.get("request_id"),
                "previous_request_id": previous_payload.get("request_id"),
            },
        )

    def diagnose_roi(self, request: RoiDiagnosisRequest) -> RoiDiagnosisResponse:
        query = ReportQuery(
            advertiser_id=request.advertiser_id,
            start_date=request.start_date,
            end_date=request.end_date,
            marketing_goal=request.marketing_goal,
            order_platform=request.order_platform,
            fields=request.fields,
            filtering=request.filtering,
            ad_ids=request.ad_ids,
            page=1,
            page_size=request.max_ads,
        )
        payload = self.get_ad_report(query)
        return self.diagnose_roi_from_rows(
            _extract_rows(payload),
            request,
            source={"endpoint": AD_REPORT_PATH, "request_id": payload.get("request_id")},
        )

    def diagnose_roi_from_rows(
        self,
        rows: Iterable[dict[str, Any]],
        request: RoiDiagnosisRequest,
        *,
        source: dict[str, Any] | None = None,
    ) -> RoiDiagnosisResponse:
        base_target_roi = request.target_roi or self.default_target_roi
        breakeven_roi = _breakeven_roi(request)
        target_roi = base_target_roi
        warnings: list[str] = []
        if breakeven_roi is not None:
            minimum_target_roi = breakeven_roi * (Decimal("1") + request.roi_safety_margin_rate)
            if target_roi < minimum_target_roi:
                warnings.append(
                    f"Target ROI {target_roi} is below profit-safe ROI {minimum_target_roi}; "
                    "using profit-safe ROI for classification."
                )
                target_roi = minimum_target_roi
        net_profit_margin = _net_profit_margin(request)
        min_spend = request.min_spend if request.min_spend is not None else self.default_min_spend
        min_clicks = request.min_clicks if request.min_clicks is not None else self.default_min_clicks
        min_orders = request.min_orders if request.min_orders is not None else self.default_min_orders
        insights: list[AdRoiInsight] = []
        proposals: list[ActionProposal] = []
        for raw_row in list(rows)[: request.max_ads]:
            insight = _build_insight(
                _flatten_row(raw_row),
                target_roi=target_roi,
                min_spend=min_spend,
                min_clicks=min_clicks,
                min_orders=min_orders,
                breakeven_roi=breakeven_roi,
                net_profit_margin=net_profit_margin,
            )
            insights.append(insight)
            proposal = _proposal_for_insight(request.advertiser_id, insight)
            if proposal is not None:
                proposals.append(proposal)
        insights.sort(key=lambda item: item.cost, reverse=True)
        proposals.sort(key=lambda item: {"pause_ad": 0, "decrease_budget": 1}.get(item.action_type, 9))
        total_cost = sum((item.cost for item in insights), Decimal("0"))
        estimated_pay_amount = sum(
            (item.pay_order_amount if item.pay_order_amount is not None else item.cost * item.roi)
            for item in insights
        )
        estimated_roi = estimated_pay_amount / total_cost if total_cost else Decimal("0")
        estimated_net_profit = (
            sum((item.estimated_net_profit or Decimal("0")) for item in insights)
            if net_profit_margin is not None
            else None
        )
        profit_roi = (
            estimated_net_profit / total_cost
            if estimated_net_profit is not None and total_cost
            else None
        )
        if not insights:
            warnings.append("No ad rows were returned.")
        summary = RoiDiagnosisSummary(
            advertiser_id=request.advertiser_id,
            start_date=request.start_date,
            end_date=request.end_date,
            target_roi=target_roi,
            total_cost=total_cost,
            estimated_pay_order_amount=estimated_pay_amount,
            estimated_roi=estimated_roi,
            breakeven_roi=breakeven_roi,
            estimated_net_profit=estimated_net_profit,
            profit_roi=profit_roi,
            ad_count=len(insights),
            scale_count=sum(1 for item in insights if item.status == "scale"),
            cut_count=sum(1 for item in insights if item.status == "cut"),
            pause_candidate_count=sum(1 for item in insights if item.status == "pause_candidate"),
            insufficient_data_count=sum(1 for item in insights if item.status == "insufficient_data"),
        )
        return RoiDiagnosisResponse(
            summary=summary,
            insights=insights,
            proposals=proposals,
            source=source or {},
            warnings=warnings,
        )

    def apply_budget_updates(self, request: BudgetUpdateRequest) -> dict[str, Any]:
        self._ensure_write_allowed(confirm=request.confirm, reason=request.reason)
        _validate_budget_updates(request.data)
        return self.client.post(
            BUDGET_UPDATE_PATH,
            access_token=self.access_token,
            json_body={
                "advertiser_id": request.advertiser_id,
                # previous_budget is a local guardrail input, not an official API field.
                "data": [{"ad_id": item.ad_id, "budget": item.budget} for item in request.data],
            },
        )

    def apply_bid_updates(self, request: BidUpdateRequest) -> dict[str, Any]:
        self._ensure_write_allowed(confirm=request.confirm, reason=request.reason)
        return self.client.post(
            BID_UPDATE_PATH,
            access_token=self.access_token,
            json_body={"advertiser_id": request.advertiser_id, "data": [item.model_dump() for item in request.data]},
        )

    def apply_roi_goal_updates(self, request: RoiGoalUpdateRequest) -> dict[str, Any]:
        self._ensure_write_allowed(confirm=request.confirm, reason=request.reason)
        return self.client.post(
            ROI_GOAL_UPDATE_PATH,
            access_token=self.access_token,
            json_body={"advertiser_id": request.advertiser_id, "data": [item.model_dump() for item in request.data]},
        )

    def update_ad_status(self, request: AdStatusUpdateRequest) -> dict[str, Any]:
        self._ensure_write_allowed(confirm=request.confirm, reason=request.reason)
        return self.client.post(
            AD_STATUS_UPDATE_PATH,
            access_token=self.access_token,
            json_body={
                "advertiser_id": request.advertiser_id,
                "ad_ids": request.ad_ids,
                "operation": request.operation,
            },
        )

    def _report_params(self, query: ReportQuery) -> dict[str, Any]:
        payload = query.model_dump(exclude_none=True)
        filtering = dict(payload.pop("filtering", {}) or {})
        for key in ("marketing_goal", "order_platform"):
            value = payload.pop(key, None)
            if value is not None:
                filtering.setdefault(key, value)
        ad_ids = payload.pop("ad_ids", [])
        if ad_ids:
            filtering["ad_ids"] = ad_ids
        # OceanEngine Qianchuan report endpoints require the filtering
        # parameter even when no filters are selected.
        payload["filtering"] = filtering
        if not payload.get("group_by"):
            payload.pop("group_by", None)
        return payload

    def _ensure_write_allowed(self, *, confirm: bool, reason: str | None) -> None:
        if not self.write_enabled:
            raise WriteDisabledError("Write APIs are disabled. Set QIANCHUAN_WRITE_ENABLED=true.")
        if not confirm:
            raise ConfirmationRequiredError("Write calls require confirm=true.")
        if not reason or len(reason.strip()) < 8:
            raise ConfirmationRequiredError("Write calls require reason with at least 8 characters.")


def analyze_material_fatigue_from_rows(
    *,
    current_rows: Iterable[dict[str, Any]],
    previous_rows: Iterable[dict[str, Any]],
    request: MaterialFatigueRequest,
    source: dict[str, Any] | None = None,
) -> MaterialFatigueResponse:
    current = _aggregate_material_rows(current_rows)
    previous = _aggregate_material_rows(previous_rows)
    warnings: list[str] = []
    if not current:
        warnings.append("No current material rows were returned.")
    if not previous:
        warnings.append("No previous material rows were returned.")

    insights: list[MaterialFatigueInsight] = []
    for material_id, current_item in sorted(
        current.items(),
        key=lambda pair: pair[1]["cost"],
        reverse=True,
    )[: request.max_materials]:
        previous_item = previous.get(material_id, _empty_material_bucket(material_id))
        status, reasons, action = _classify_material_fatigue(
            current_item=current_item,
            previous_item=previous_item,
            request=request,
        )
        insights.append(
            MaterialFatigueInsight(
                material_id=material_id,
                material_name=current_item.get("name") or previous_item.get("name"),
                current_cost=current_item["cost"],
                previous_cost=previous_item["cost"],
                current_impressions=current_item["shows"],
                previous_impressions=previous_item["shows"],
                current_clicks=current_item["clicks"],
                previous_clicks=previous_item["clicks"],
                current_ctr=_ctr(current_item),
                previous_ctr=_ctr(previous_item),
                current_roi=_roi(current_item),
                previous_roi=_roi(previous_item),
                current_pay_order_amount=current_item["pay_amount"],
                previous_pay_order_amount=previous_item["pay_amount"],
                status=status,
                reasons=reasons,
                recommended_action=action,
            )
        )

    insights.sort(
        key=lambda item: (
            {"fatigue": 0, "winner": 1, "watch": 2, "insufficient_data": 3}[item.status],
            -item.current_cost,
        )
    )
    return MaterialFatigueResponse(
        advertiser_id=request.advertiser_id,
        current_start_date=request.current_start_date,
        current_end_date=request.current_end_date,
        previous_start_date=request.previous_start_date,
        previous_end_date=request.previous_end_date,
        total_materials=len(insights),
        fatigue_count=sum(1 for item in insights if item.status == "fatigue"),
        winner_count=sum(1 for item in insights if item.status == "winner"),
        insufficient_data_count=sum(1 for item in insights if item.status == "insufficient_data"),
        insights=insights,
        source=source or {},
        warnings=warnings,
    )


def _extract_plan_product_ids(detail_data: dict[str, Any]) -> set[str]:
    product_ids: set[str] = set()
    for field in ("product_infos", "multi_product_creative_list"):
        rows = detail_data.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = row.get("product_id") or row.get("id")
            if value not in (None, "", 0, "0"):
                product_ids.add(str(value))
    raw_ids = detail_data.get("product_ids")
    if isinstance(raw_ids, list):
        product_ids.update(str(value) for value in raw_ids if value not in (None, "", 0, "0"))
    return product_ids


def _video_review_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "aweme_item_id": item.get("aweme_item_id"),
        "material_id": item.get("material_id"),
        "title": item.get("title"),
        "is_recommend": item.get("is_recommend"),
        "view_cnt": item.get("view_cnt"),
        "like_cnt": item.get("like_cnt"),
        "duration": item.get("duration"),
        "video_cover_url": item.get("video_cover_url"),
    }


def _video_candidate_root_cause(
    *,
    published_rows: list[dict[str, Any]],
    permission_error: dict[str, Any] | None,
    account_video_diagnostic: dict[str, Any] | None,
) -> str:
    if permission_error is not None:
        return "product_video_endpoint_permission_missing"
    if published_rows:
        return "product_linked_videos_available"
    if account_video_diagnostic and int(account_video_diagnostic.get("count") or 0) > 0:
        return "videos_exist_but_none_are_linked_to_the_requested_product"
    return "no_visible_videos_for_the_aweme_account"


def _material_query(
    request: MaterialFatigueRequest,
    *,
    start_date,
    end_date,
) -> ReportQuery:
    return ReportQuery(
        advertiser_id=request.advertiser_id,
        start_date=start_date,
        end_date=end_date,
        marketing_goal=request.marketing_goal,
        order_platform=request.order_platform,
        fields=request.fields,
        filtering=request.filtering,
        ad_ids=request.ad_ids,
        page=1,
        page_size=request.page_size,
    )


def _net_profit_margin(request: RoiDiagnosisRequest) -> Decimal | None:
    if request.gross_margin_rate is None:
        return None
    return request.gross_margin_rate * (Decimal("1") - request.refund_rate) - request.extra_cost_rate


def _breakeven_roi(request: RoiDiagnosisRequest) -> Decimal | None:
    net_margin = _net_profit_margin(request)
    if net_margin is None:
        return None
    if net_margin <= 0:
        raise ValidationError("Net profit margin must be greater than zero.")
    return Decimal("1") / net_margin


def _build_insight(
    row: dict[str, Any],
    *,
    target_roi: Decimal,
    min_spend: Decimal,
    min_clicks: int,
    min_orders: int,
    breakeven_roi: Decimal | None,
    net_profit_margin: Decimal | None,
) -> AdRoiInsight:
    ad_id = str(row.get("ad_id") or row.get("id") or row.get("adgroup_id") or "unknown")
    cost = _first_decimal(row, COST_KEYS) or Decimal("0")
    roi_metric, roi = _first_decimal_with_key(row, ROI_KEYS)
    pay_amount = _first_decimal(row, PAY_AMOUNT_KEYS)
    if roi == 0 and pay_amount is not None and cost > 0:
        roi = pay_amount / cost
        roi_metric = "computed_pay_amount_roi"
    if pay_amount is None:
        pay_amount = cost * roi
    estimated_net_profit = (
        pay_amount * net_profit_margin - cost
        if net_profit_margin is not None
        else None
    )
    profit_roi = estimated_net_profit / cost if estimated_net_profit is not None and cost > 0 else None
    orders = _first_int(row, ORDER_KEYS)
    clicks = _first_int(row, CLICK_KEYS)
    status_value, reasons = _classify_ad(
        cost=cost,
        roi=roi,
        target_roi=target_roi,
        orders=orders,
        clicks=clicks,
        min_spend=min_spend,
        min_clicks=min_clicks,
        min_orders=min_orders,
        estimated_net_profit=estimated_net_profit,
    )
    return AdRoiInsight(
        ad_id=ad_id,
        ad_name=_optional_str(row.get("ad_name") or row.get("name")),
        cost=cost,
        roi=roi,
        roi_metric=roi_metric,
        target_roi=target_roi,
        pay_order_count=orders,
        click_count=clicks,
        pay_order_amount=pay_amount,
        conversion_cost=_first_decimal(row, CONVERSION_COST_KEYS),
        breakeven_roi=breakeven_roi,
        estimated_net_profit=estimated_net_profit,
        profit_roi=profit_roi,
        status=status_value,
        reasons=reasons,
    )


def _classify_ad(
    *,
    cost: Decimal,
    roi: Decimal,
    target_roi: Decimal,
    orders: int,
    clicks: int,
    min_spend: Decimal,
    min_clicks: int,
    min_orders: int,
    estimated_net_profit: Decimal | None,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if cost < min_spend:
        reasons.append(f"Spend {cost} is below threshold {min_spend}.")
    if clicks < min_clicks and orders < min_orders:
        reasons.append(f"Clicks/orders {clicks}/{orders} are below thresholds.")
    if reasons:
        return "insufficient_data", reasons
    if estimated_net_profit is not None and estimated_net_profit < 0 and orders >= min_orders:
        return "cut", [f"Estimated net profit {estimated_net_profit} is negative after ad cost."]
    if orders <= 0 and clicks >= min_clicks and cost >= min_spend:
        return "pause_candidate", ["Spent enough and clicked enough, but no paid orders."]
    if roi >= target_roi * Decimal("1.2") and orders >= min_orders:
        return "scale", [f"ROI {roi} is at least 20% above target {target_roi}."]
    if roi < target_roi * Decimal("0.75") and orders >= min_orders:
        return "cut", [f"ROI {roi} is more than 25% below target {target_roi}."]
    if roi < target_roi:
        return "watch", [f"ROI {roi} is below target {target_roi}, but not a hard cut."]
    return "watch", [f"ROI {roi} is near target {target_roi}."]


def _proposal_for_insight(advertiser_id: int, insight: AdRoiInsight) -> ActionProposal | None:
    guardrails = [
        "Writes require QIANCHUAN_WRITE_ENABLED=true.",
        "Writes require confirm=true and reason.",
        "Budget updates should obey OceanEngine platform constraints.",
    ]
    ad_id_payload: int | str = int(insight.ad_id) if insight.ad_id.isdigit() else insight.ad_id
    if insight.status == "scale":
        return ActionProposal(
            action_type="increase_budget",
            object_type="ad",
            object_id=insight.ad_id,
            title="Controlled budget scale candidate",
            reason="The ad is above ROI target with enough conversion evidence.",
            risk_level="medium",
            payload={"advertiser_id": advertiser_id, "data": [{"ad_id": ad_id_payload, "suggested_budget_multiplier": "1.20"}]},
            guardrails=guardrails,
        )
    if insight.status == "cut":
        return ActionProposal(
            action_type="decrease_budget",
            object_type="ad",
            object_id=insight.ad_id,
            title="Budget reduction candidate",
            reason="The ad is materially below target ROI.",
            risk_level="medium",
            payload={"advertiser_id": advertiser_id, "data": [{"ad_id": ad_id_payload, "suggested_budget_multiplier": "0.80"}]},
            guardrails=guardrails,
        )
    if insight.status == "pause_candidate":
        return ActionProposal(
            action_type="pause_ad",
            object_type="ad",
            object_id=insight.ad_id,
            title="Stop-loss pause candidate",
            reason="The ad spent and received clicks but produced no paid orders.",
            risk_level="high",
            payload={"advertiser_id": advertiser_id, "ad_ids": [ad_id_payload], "operation": "DISABLE"},
            guardrails=guardrails,
        )
    return None


def _validate_budget_updates(items: Iterable[BudgetUpdateItem]) -> None:
    for item in items:
        if item.previous_budget is not None and abs(item.budget - item.previous_budget) < Decimal("100"):
            raise ValidationError("Budget update delta must be at least 100 when previous_budget is provided.")


def _aggregate_material_rows(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for raw_row in rows:
        row = _flatten_row(raw_row)
        material_id = _first_text(row, MATERIAL_ID_KEYS) or "unknown"
        bucket = buckets.setdefault(material_id, _empty_material_bucket(material_id))
        bucket["name"] = _first_text(row, MATERIAL_NAME_KEYS) or bucket["name"]
        cost = _first_decimal(row, COST_KEYS) or Decimal("0")
        shows = _first_int(row, SHOW_KEYS)
        clicks = _first_int(row, CLICK_KEYS)
        pay_amount = _first_decimal(row, PAY_AMOUNT_KEYS)
        roi = _first_decimal(row, ROI_KEYS)
        if pay_amount is None and roi is not None:
            pay_amount = cost * roi
        bucket["cost"] += cost
        bucket["shows"] += shows
        bucket["clicks"] += clicks
        bucket["pay_amount"] += pay_amount or Decimal("0")
    return buckets


def _empty_material_bucket(material_id: str) -> dict[str, Any]:
    return {
        "material_id": material_id,
        "name": None,
        "cost": Decimal("0"),
        "shows": 0,
        "clicks": 0,
        "pay_amount": Decimal("0"),
    }


def _classify_material_fatigue(
    *,
    current_item: dict[str, Any],
    previous_item: dict[str, Any],
    request: MaterialFatigueRequest,
) -> tuple[str, list[str], str]:
    reasons: list[str] = []
    if current_item["cost"] < request.min_cost:
        reasons.append(f"Current spend {current_item['cost']} is below threshold {request.min_cost}.")
    if current_item["shows"] < request.min_impressions:
        reasons.append(
            f"Current impressions {current_item['shows']} are below threshold {request.min_impressions}."
        )
    if reasons:
        return "insufficient_data", reasons, "Keep collecting data before changing this material."

    previous_ctr = _ctr(previous_item)
    previous_roi = _roi(previous_item)
    current_ctr = _ctr(current_item)
    current_roi = _roi(current_item)
    if previous_item["shows"] <= 0 or previous_item["cost"] <= 0:
        return "watch", ["No usable previous baseline."], "Keep monitoring against the next baseline window."

    ctr_drop = previous_ctr > 0 and current_ctr <= previous_ctr * (Decimal("1") - request.ctr_drop_rate)
    roi_drop = previous_roi > 0 and current_roi <= previous_roi * (Decimal("1") - request.roi_drop_rate)
    if ctr_drop and roi_drop:
        return (
            "fatigue",
            [f"CTR dropped from {previous_ctr} to {current_ctr}.", f"ROI dropped from {previous_roi} to {current_roi}."],
            "Replace or rotate this material before scaling budget.",
        )
    if current_roi >= previous_roi * Decimal("1.2") and current_ctr >= previous_ctr:
        return (
            "winner",
            ["Current ROI and CTR are both holding or improving."],
            "Clone the winning angle and create controlled variants.",
        )
    return "watch", ["No strong fatigue or winner signal."], "Keep this material in observation."


def _ctr(item: dict[str, Any]) -> Decimal:
    shows = item["shows"]
    return Decimal(item["clicks"]) / Decimal(shows) if shows else Decimal("0")


def _roi(item: dict[str, Any]) -> Decimal:
    cost = item["cost"]
    return item["pay_amount"] / cost if cost else Decimal("0")


def _extract_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.insert(0, data)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("list", "rows", "items", "data_list"):
            value = candidate.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict):
            flattened.update({nested_key: nested_value for nested_key, nested_value in value.items()})
        else:
            flattened[key] = value
    return flattened


def _first_decimal(row: dict[str, Any], keys: Iterable[str]) -> Decimal | None:
    key, value = _first_decimal_with_key(row, keys)
    return value if key != "missing" else None


def _first_decimal_with_key(row: dict[str, Any], keys: Iterable[str]) -> tuple[str, Decimal]:
    for key in keys:
        if key not in row or row[key] in (None, ""):
            continue
        try:
            value = Decimal(str(row[key]))
        except (InvalidOperation, ValueError):
            continue
        if not value.is_finite():
            continue
        return key, value
    return "missing", Decimal("0")


def _first_int(row: dict[str, Any], keys: Iterable[str]) -> int:
    value = _first_decimal(row, keys)
    return int(value) if value is not None else 0


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(row: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        if key in row:
            text = _optional_str(row.get(key))
            if text:
                return text
    return None
