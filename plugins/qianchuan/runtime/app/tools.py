from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from app.qianchuan_official_catalog import OFFICIAL_QIANCHUAN_INTERFACES


DOC_ROOT = "https://open.oceanengine.com/labels/12"
TOOL_LIST_DOC = f"{DOC_ROOT}/docs/1847297003631945"


@dataclass(frozen=True)
class ToolSpec:
    key: str
    title: str
    method: Literal["GET", "POST"]
    path: str
    risk_level: Literal["low", "medium", "high"]
    write: bool
    docs_url: str
    capabilities: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["capabilities"] = list(self.capabilities)
        return payload


_GUARDED_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("qianchuan_oauth_advertiser_get_v1", "List authorized accounts", "GET", "/oauth2/advertiser/get/", "low", False, f"{DOC_ROOT}/docs/1697467748096067", ("account_discovery",)),
    ToolSpec("qianchuan_product_available_get_v1", "List available merchant products", "GET", "/v1.0/qianchuan/product/available/get/", "low", False, TOOL_LIST_DOC, ("product_discovery", "inventory_read")),
    ToolSpec("qianchuan_aweme_product_available_get_v1", "List products available to an Aweme account", "GET", "/v1.0/qianchuan/aweme/product/available/get/", "low", False, TOOL_LIST_DOC, ("product_discovery",)),
    ToolSpec("qianchuan_aweme_authorized_get_v1", "List authorized Aweme accounts", "GET", "/v1.0/qianchuan/aweme/authorized/get/", "low", False, TOOL_LIST_DOC, ("aweme_discovery",)),
    ToolSpec("qianchuan_aweme_video_get_v1", "List shop videos for Qianchuan Aweme-order/SXT workflows; not PC uni-promotion", "GET", "/v1.0/qianchuan/aweme/video/get/", "low", False, TOOL_LIST_DOC, ("material_read", "video_discovery", "aweme_order_analysis")),
    ToolSpec("qianchuan_file_video_aweme_get_v1", "List existing Aweme videos with optional product filtering", "GET", "/v1.0/qianchuan/file/video/aweme/get/", "low", False, TOOL_LIST_DOC, ("material_read", "video_discovery", "full_domain_analysis")),
    ToolSpec("qianchuan_carousel_get_v1", "List Qianchuan carousel (图文) materials", "GET", "/v1.0/qianchuan/carousel/get/", "low", False, TOOL_LIST_DOC, ("material_read", "carousel_discovery", "full_domain_analysis")),
    ToolSpec("qianchuan_carousel_aweme_get_v1", "List published Aweme carousel (图文) materials", "GET", "/v1.0/qianchuan/carousel/aweme/get/", "low", False, TOOL_LIST_DOC, ("material_read", "carousel_discovery", "full_domain_analysis")),
    ToolSpec("qianchuan_uni_aweme_authorized_get_v1", "List all-domain authorized Aweme accounts", "GET", "/v1.0/qianchuan/uni_aweme/authorized/get/", "low", False, TOOL_LIST_DOC, ("aweme_discovery", "full_domain_analysis")),
    ToolSpec("qianchuan_uni_aweme_suggest_budget_v1", "Get uni-aweme suggested budget", "GET", "/v1.0/qianchuan/uni_aweme/suggest/budget/", "low", False, TOOL_LIST_DOC, ("budget_suggestion", "full_domain_analysis")),
    ToolSpec("qianchuan_uni_aweme_suggest_roi_v1", "Get uni-aweme suggested ROI", "GET", "/v1.0/qianchuan/uni_aweme/suggest/roi/", "low", False, TOOL_LIST_DOC, ("roi_goal_suggestion", "full_domain_analysis")),
    ToolSpec("qianchuan_uni_promotion_authorizable_shop_list_v1", "List shops authorizable for product full-domain", "GET", "/v1.0/qianchuan/uni_promotion/authorizable_shop/list/", "low", False, TOOL_LIST_DOC, ("shop_discovery", "full_domain_authorization")),
    ToolSpec("qianchuan_uni_promotion_authorization_apply_v1", "Apply for product full-domain control authorization", "POST", "/v1.0/qianchuan/uni_promotion/authorization/apply/", "high", True, TOOL_LIST_DOC, ("full_domain_authorization",)),
    ToolSpec("qianchuan_account_balance_get_v1", "Get Qianchuan account balance", "GET", "/v1.0/qianchuan/account/balance/get/", "low", False, TOOL_LIST_DOC, ("balance_read", "launch_preflight")),
    ToolSpec("qianchuan_account_budget_update_v1", "Update account budget", "POST", "/v1.0/qianchuan/account/budget/update/", "high", True, TOOL_LIST_DOC, ("account_budget_update",)),
    ToolSpec("qianchuan_ad_quota_get_v1", "Get active ad quota", "GET", "/v1.0/qianchuan/ad/quota/get/", "low", False, TOOL_LIST_DOC, ("quota_read", "launch_preflight")),
    ToolSpec("qianchuan_ad_reject_reason_v1", "Get ad audit rejection reasons", "GET", "/v1.0/qianchuan/ad/reject_reason/", "low", False, TOOL_LIST_DOC, ("audit_status_read",)),
    ToolSpec("qianchuan_ad_learning_status_get_v1", "Get ad learning status", "GET", "/v1.0/qianchuan/ad/learing_status/get/", "low", False, TOOL_LIST_DOC, ("learning_status_read", "launch_followup")),
    ToolSpec("qianchuan_campaign_create_v1", "Create campaign", "POST", "/v1.0/qianchuan/campaign/create/", "high", True, TOOL_LIST_DOC, ("campaign_create",)),
    ToolSpec("qianchuan_campaign_update_v1", "Update campaign", "POST", "/v1.0/qianchuan/campaign/update/", "high", True, TOOL_LIST_DOC, ("campaign_update",)),
    ToolSpec("qianchuan_campaign_list_get_v1", "List campaigns", "GET", "/v1.0/qianchuan/campaign_list/get/", "low", False, TOOL_LIST_DOC, ("campaign_read",)),
    ToolSpec("qianchuan_ad_create_v1", "Create ad with creative rules", "POST", "/v1.0/qianchuan/ad/create/", "high", True, TOOL_LIST_DOC, ("ad_create", "creative_create")),
    ToolSpec("qianchuan_ad_update_v1", "Update ad", "POST", "/v1.0/qianchuan/ad/update/", "high", True, TOOL_LIST_DOC, ("ad_update",)),
    ToolSpec("qianchuan_ad_overall_marketing_update_v1", "Upgrade a full-domain plan to a multiplier plan (legacy alias)", "POST", "/v1.0/qianchuan/ad/overall_marketing/update/", "high", True, f"{DOC_ROOT}/docs/1866761206705753", ("ad_update", "multiplier_upgrade")),
    ToolSpec("qianchuan_uni_promotion_multiplier_upgrade_v1", "Upgrade one paused PC uni-promotion plan to a multiplier plan", "POST", "/v1.0/qianchuan/ad/overall_marketing/update/", "high", True, f"{DOC_ROOT}/docs/1866761206705753", ("ad_update", "multiplier_upgrade")),
    ToolSpec("qianchuan_ad_region_update_v1", "Update ad region targeting", "POST", "/v1.0/qianchuan/ad/region/update/", "high", True, TOOL_LIST_DOC, ("ad_update", "targeting_update")),
    ToolSpec("qianchuan_ad_schedule_date_update_v1", "Update ad schedule dates", "POST", "/v1.0/qianchuan/ad/schedule_date/update/", "high", True, TOOL_LIST_DOC, ("ad_update", "schedule_update")),
    ToolSpec("qianchuan_ad_schedule_fixed_range_update_v1", "Update ad fixed delivery hours", "POST", "/v1.0/qianchuan/ad/schedule_fixed_range/update/", "high", True, TOOL_LIST_DOC, ("ad_update", "schedule_update")),
    ToolSpec("qianchuan_ad_get_v1", "List ads", "GET", "/v1.0/qianchuan/ad/get/", "low", False, TOOL_LIST_DOC, ("ad_read",)),
    ToolSpec("qianchuan_ad_detail_get_v1", "Get ad detail", "GET", "/v1.0/qianchuan/ad/detail/get/", "low", False, TOOL_LIST_DOC, ("ad_read", "creative_read")),
    ToolSpec("qianchuan_batch_campaign_status_update_v1", "Batch update campaign status", "POST", "/v1.0/qianchuan/batch_campaign_status/update/", "high", True, TOOL_LIST_DOC, ("campaign_update", "status_update")),
    ToolSpec("qianchuan_ad_material_get_v1", "Get ad materials", "GET", "/v1.0/qianchuan/ad/material/get/", "low", False, TOOL_LIST_DOC, ("material_read",)),
    ToolSpec("qianchuan_ad_material_delete_v1", "Delete ad material", "POST", "/v1.0/qianchuan/ad/material/delete/", "high", True, TOOL_LIST_DOC, ("material_delete",)),
    ToolSpec("qianchuan_material_get_v1", "Get material library", "GET", "/v1.0/qianchuan/material/get/", "low", False, TOOL_LIST_DOC, ("material_read",)),
    ToolSpec("qianchuan_material_ad_get_v1", "Get material related ads", "GET", "/v1.0/qianchuan/material/ad/get/", "low", False, TOOL_LIST_DOC, ("material_read", "ad_read")),
    ToolSpec("qianchuan_image_get_v1", "Get images", "GET", "/v1.0/qianchuan/image/get/", "low", False, TOOL_LIST_DOC, ("material_read",)),
    ToolSpec("qianchuan_image_delete_v1", "Delete uploaded images", "POST", "/v1.0/qianchuan/image/delete/", "high", True, TOOL_LIST_DOC, ("material_delete",)),
    ToolSpec("qianchuan_video_get_v1", "Get Qianchuan video materials", "GET", "/v1.0/qianchuan/video/get/", "low", False, TOOL_LIST_DOC, ("material_read", "video_discovery")),
    ToolSpec("qianchuan_file_video_ad_v2", "Upload one video material", "POST", "/2/file/video/ad/", "high", True, TOOL_LIST_DOC, ("material_upload",)),
    ToolSpec("qianchuan_file_image_ad_v2", "Upload one image material", "POST", "/2/file/image/ad/", "high", True, TOOL_LIST_DOC, ("material_upload",)),
    ToolSpec("qianchuan_carousel_create_v2", "Create a 图文 carousel material from uploaded images", "POST", "/2/carousel/create/", "high", True, TOOL_LIST_DOC, ("carousel_create", "material_upload")),
    ToolSpec("qianchuan_uni_aweme_ad_create_v1", "Create uni aweme ad", "POST", "/v1.0/qianchuan/uni_aweme/ad/create/", "high", True, TOOL_LIST_DOC, ("ad_create",)),
    ToolSpec("qianchuan_uni_aweme_ad_update_v1", "Update uni aweme ad", "POST", "/v1.0/qianchuan/uni_aweme/ad/update/", "high", True, TOOL_LIST_DOC, ("ad_update",)),
    ToolSpec("qianchuan_uni_promotion_list_v1", "List uni-promotion ads", "GET", "/v1.0/qianchuan/uni_promotion/list/", "low", False, TOOL_LIST_DOC, ("ad_read", "full_domain_analysis")),
    ToolSpec("qianchuan_uni_promotion_ad_detail_v1", "Get uni-promotion ad detail", "GET", "/v1.0/qianchuan/uni_promotion/ad/detail/", "low", False, TOOL_LIST_DOC, ("ad_read", "full_domain_analysis")),
    ToolSpec("qianchuan_uni_promotion_product_get_v1", "List products available for uni-promotion", "GET", "/v1.0/qianchuan/uni_promotion/product/get/", "low", False, TOOL_LIST_DOC, ("product_discovery", "inventory_read", "full_domain_analysis")),
    ToolSpec("qianchuan_uni_promotion_ad_budget_update_v1", "Update uni-promotion ad budget", "POST", "/v1.0/qianchuan/uni_promotion/ad/budget/update/", "high", True, TOOL_LIST_DOC, ("budget_update",)),
    ToolSpec("qianchuan_uni_promotion_ad_roi2_goal_update_v1", "Update uni-promotion ad ROI2 goal", "POST", "/v1.0/qianchuan/uni_promotion/ad/roi2_goal/update/", "high", True, TOOL_LIST_DOC, ("roi_goal_update",)),
    ToolSpec("qianchuan_uni_promotion_ad_status_update_v1", "Update uni-promotion ad status", "POST", "/v1.0/qianchuan/uni_promotion/ad/status/update/", "high", True, TOOL_LIST_DOC, ("pause_ad", "enable_ad")),
    ToolSpec("qianchuan_uni_promotion_ad_name_update_v1", "Update uni-promotion ad name", "POST", "/v1.0/qianchuan/uni_promotion/ad/name/update/", "high", True, TOOL_LIST_DOC, ("ad_update",)),
    ToolSpec("qianchuan_uni_promotion_ad_schedule_date_update_v1", "Update uni-promotion schedule", "POST", "/v1.0/qianchuan/uni_promotion/ad/schedule_date/update/", "high", True, TOOL_LIST_DOC, ("ad_update",)),
    ToolSpec("qianchuan_uni_promotion_ad_suggestion_v1", "Get uni-promotion audit suggestions", "GET", "/v1.0/qianchuan/uni_promotion/ad/suggestion/", "low", False, TOOL_LIST_DOC, ("audit_status_read", "full_domain_analysis")),
    ToolSpec("qianchuan_uni_promotion_auth_init_v1", "Initialize uni-promotion authorization", "POST", "/v1.0/qianchuan/uni_promotion/auth/init/", "high", True, TOOL_LIST_DOC, ("full_domain_authorization",)),
    ToolSpec("qianchuan_aweme_order_create_v1", "Create aweme order", "POST", "/v1.0/qianchuan/aweme/order/create/", "high", True, TOOL_LIST_DOC, ("order_create",)),
    ToolSpec("qianchuan_aweme_order_budget_add_v1", "Add Aweme order budget", "POST", "/v1.0/qianchuan/aweme/order/budget/add/", "high", True, TOOL_LIST_DOC, ("order_budget_update",)),
    ToolSpec("qianchuan_aweme_uni_promotion_order_create_v1", "Create aweme uni promotion order", "POST", "/v1.0/qianchuan/aweme/uni_promotion/order/create/", "high", True, TOOL_LIST_DOC, ("order_create",)),
    ToolSpec("qianchuan_aweme_uni_promotion_order_budget_add_v1", "Add Aweme uni-promotion order budget", "POST", "/v1.0/qianchuan/aweme/uni_promotion/order/budget/add/", "high", True, TOOL_LIST_DOC, ("order_budget_update",)),
    ToolSpec("qianchuan_tools_smart_boost_ad_boost_set_v1", "Set smart-boost optimization", "POST", "/v1.0/qianchuan/tools/smart_boost/ad_boost/set/", "high", True, TOOL_LIST_DOC, ("smart_boost_update",)),
    ToolSpec("qianchuan_aweme_uni_promotion_ad_material_get_v1", "Get aweme uni promotion ad material", "GET", "/v1.0/qianchuan/aweme/uni_promotion/ad/material/get/", "low", False, TOOL_LIST_DOC, ("material_read",)),
    ToolSpec("qianchuan_uni_promotion_ad_control_task_create_v1", "Create uni promotion control task", "POST", "/v1.0/qianchuan/uni_promotion/ad/control_task/create/", "high", True, TOOL_LIST_DOC, ("control_task_create",)),
    ToolSpec("qianchuan_uni_promotion_ad_control_task_smart_control_create_v1", "Create uni promotion smart control task", "POST", "/v1.0/qianchuan/uni_promotion/ad/control_task/smart_control/create/", "high", True, TOOL_LIST_DOC, ("control_task_create",)),
    ToolSpec("qianchuan_uni_promotion_ad_control_task_list_v1", "List uni-promotion control tasks", "GET", "/v1.0/qianchuan/uni_promotion/ad/control_task/list/", "low", False, TOOL_LIST_DOC, ("control_task_read", "full_domain_analysis")),
    ToolSpec("qianchuan_uni_promotion_ad_control_task_update_v1", "Update uni-promotion control task", "POST", "/v1.0/qianchuan/uni_promotion/ad/control_task/update/", "high", True, TOOL_LIST_DOC, ("control_task_update",)),
    ToolSpec("qianchuan_uni_promotion_ad_control_task_status_update_v1", "Update uni-promotion control task status", "POST", "/v1.0/qianchuan/uni_promotion/ad/control_task/status/update/", "high", True, TOOL_LIST_DOC, ("control_task_update",)),
    ToolSpec("qianchuan_uni_promotion_ad_control_task_budget_update_v1", "Update uni-promotion control task budget", "POST", "/v1.0/qianchuan/uni_promotion/ad/control_task/budget/update/", "high", True, TOOL_LIST_DOC, ("control_task_update", "budget_update")),
    ToolSpec("qianchuan_uni_promotion_ad_control_task_duration_update_v1", "Update uni-promotion control task duration", "POST", "/v1.0/qianchuan/uni_promotion/ad/control_task/duration/update/", "high", True, TOOL_LIST_DOC, ("control_task_update",)),
    ToolSpec("qianchuan_uni_promotion_ad_control_task_smart_control_status_update_v1", "Update smart-control task status", "POST", "/v1.0/qianchuan/uni_promotion/ad/control_task/smart_control/status/update/", "high", True, TOOL_LIST_DOC, ("control_task_update",)),
    ToolSpec("qianchuan_uni_promotion_ad_material_add_v1", "Add uni promotion ad material", "POST", "/v1.0/qianchuan/uni_promotion/ad/material/add/", "high", True, TOOL_LIST_DOC, ("material_bind",)),
    ToolSpec("qianchuan_uni_promotion_ad_material_delete_v1", "Delete uni promotion ad material", "POST", "/v1.0/qianchuan/uni_promotion/ad/material/delete/", "high", True, TOOL_LIST_DOC, ("material_delete",)),
    ToolSpec("qianchuan_uni_promotion_ad_material_get_v1", "Get uni promotion ad material", "GET", "/v1.0/qianchuan/uni_promotion/ad/material/get/", "low", False, TOOL_LIST_DOC, ("material_read",)),
    ToolSpec("qianchuan_uni_promotion_ad_product_get_v1", "Get uni promotion ad products", "GET", "/v1.0/qianchuan/uni_promotion/ad/product/get/", "low", False, TOOL_LIST_DOC, ("product_read",)),
    ToolSpec("qianchuan_uni_promotion_ad_product_delete_v1", "Delete product from uni-promotion ad", "POST", "/v1.0/qianchuan/uni_promotion/ad/product/delete/", "high", True, TOOL_LIST_DOC, ("product_delete",)),
    ToolSpec("qianchuan_uni_promotion_product_aweme_get_v1", "Get Aweme accounts for uni-promotion product", "GET", "/v1.0/qianchuan/uni_promotion/product/aweme/get/", "low", False, TOOL_LIST_DOC, ("aweme_discovery", "product_read", "full_domain_analysis")),
    ToolSpec("qianchuan_uni_promotion_block_material_get_v1", "List full-domain videos or carousels eligible for exclusion", "GET", "/v1.0/qianchuan/uni_promotion/block_material/get/", "low", False, TOOL_LIST_DOC, ("material_read", "video_discovery", "full_domain_analysis")),
    ToolSpec("qianchuan_report_advertiser_get_v1", "Get account report", "GET", "/v1.0/qianchuan/report/advertiser/get/", "low", False, f"{DOC_ROOT}/docs/1697466393573376", ("roi_analysis",)),
    ToolSpec("qianchuan_report_ad_get_v1", "Get ad report", "GET", "/v1.0/qianchuan/report/ad/get/", "low", False, f"{DOC_ROOT}/docs/1697466415173644", ("roi_analysis", "budget_decision")),
    ToolSpec("qianchuan_report_material_get_v1", "Get material report", "GET", "/v1.0/qianchuan/report/material/get/", "low", False, f"{DOC_ROOT}/docs/1745207002572807", ("creative_analysis",)),
    ToolSpec("qianchuan_report_search_word_get_v1", "Get search word report", "GET", "/v1.0/qianchuan/report/search_word/get/", "low", False, f"{DOC_ROOT}/docs/1762139117875212", ("keyword_analysis",)),
    ToolSpec("qianchuan_product_analyse_list_v1", "List product competition analyses", "GET", "/v1.0/qianchuan/product/analyse/list/", "low", False, f"{DOC_ROOT}/docs/1766108954024968", ("product_competition_analysis", "product_analysis", "optimization_read")),
    ToolSpec("qianchuan_product_analyse_compare_stats_data_v1", "Compare product competition performance", "GET", "/v1.0/qianchuan/product/analyse/compare_stats_data/", "low", False, f"{DOC_ROOT}/docs/1766109528633420", ("product_competition_analysis", "roi_analysis", "optimization_read")),
    ToolSpec("qianchuan_product_analyse_compare_creative_v1", "Compare product competition creatives", "GET", "/v1.0/qianchuan/product/analyse/compare_creative/", "low", False, f"{DOC_ROOT}/docs/1766109809437763", ("product_competition_analysis", "creative_analysis", "optimization_read")),
    ToolSpec("qianchuan_keyword_check_v1", "Validate Qianchuan keywords", "POST", "/v1.0/qianchuan/keyword/check/", "low", False, TOOL_LIST_DOC, ("keyword_validation", "optimization_read")),
    ToolSpec("qianchuan_report_uni_promotion_config_get_v1", "Get uni-promotion report fields", "GET", "/v1.0/qianchuan/report/uni_promotion/config/get/", "low", False, f"{DOC_ROOT}/docs/1823296280645708", ("full_domain_analysis", "report_schema_read")),
    ToolSpec("qianchuan_report_uni_promotion_data_get_v1", "Get uni promotion report", "GET", "/v1.0/qianchuan/report/uni_promotion/data/get/", "low", False, f"{DOC_ROOT}/docs/1823297941140569", ("full_domain_analysis",)),
    ToolSpec("qianchuan_suggest_roi_goal_v1", "Get suggested ROI goal", "GET", "/v1.0/qianchuan/suggest/roi/goal/", "low", False, f"{DOC_ROOT}/docs/1730170307692548", ("roi_goal_suggestion",)),
    ToolSpec("qianchuan_suggest_budget_v1", "Get suggested budget", "GET", "/v1.0/qianchuan/suggest/budget/", "low", False, f"{DOC_ROOT}/docs/1770008006444036", ("budget_suggestion",)),
    ToolSpec("qianchuan_estimate_effect_v1", "Estimate delivery effect", "GET", "/v1.0/qianchuan/estimate/effect/", "low", False, f"{DOC_ROOT}/docs/1770010949341327", ("change_simulation",)),
    ToolSpec("qianchuan_ad_budget_update_v1", "Update ad budget", "POST", "/v1.0/qianchuan/ad/budget/update/", "high", True, f"{DOC_ROOT}/docs/1697467207831565", ("budget_update",)),
    ToolSpec("qianchuan_ad_bid_update_v1", "Update ad bid", "POST", "/v1.0/qianchuan/ad/bid/update/", "high", True, f"{DOC_ROOT}/docs/1697467222614023", ("bid_update",)),
    ToolSpec("qianchuan_roi_goal_update_v1", "Update ROI goal", "POST", "/v1.0/qianchuan/roi/goal/update/", "high", True, f"{DOC_ROOT}/docs/1730170501456973", ("roi_goal_update",)),
    ToolSpec("qianchuan_ad_status_update_v1", "Update ad status", "POST", "/v1.0/qianchuan/ad/status/update/", "high", True, f"{DOC_ROOT}/docs/1847297003631945", ("pause_ad", "enable_ad")),
)


def _official_catalog_specs() -> tuple[ToolSpec, ...]:
    guarded_keys = {item.key for item in _GUARDED_TOOL_SPECS}
    guarded_paths = {item.path for item in _GUARDED_TOOL_SPECS}
    items: list[ToolSpec] = []
    for key, method, path in OFFICIAL_QIANCHUAN_INTERFACES:
        if key in guarded_keys or path in guarded_paths:
            continue
        write = method == "POST"
        items.append(
            ToolSpec(
                key=key,
                title=key.removeprefix("qianchuan_").replace("_", " "),
                method=method,  # type: ignore[arg-type]
                path=path,
                risk_level="high" if write else "low",
                write=write,
                docs_url=TOOL_LIST_DOC,
                capabilities=("official_catalog", "raw_write" if write else "read"),
            )
        )
    return tuple(items)


TOOL_SPECS: tuple[ToolSpec, ...] = _GUARDED_TOOL_SPECS + _official_catalog_specs()


def list_tools() -> list[dict[str, object]]:
    return [item.to_dict() for item in TOOL_SPECS]


def get_tool(key: str) -> ToolSpec | None:
    return next((item for item in TOOL_SPECS if item.key == key), None)
