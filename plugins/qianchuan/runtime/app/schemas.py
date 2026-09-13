from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_ROI_REPORT_FIELDS: tuple[str, ...] = (
    "stat_cost",
    "show_cnt",
    "click_cnt",
    "ctr",
    "pay_order_count",
    "pay_order_amount",
    "prepay_and_pay_order_roi",
    "all_order_pay_roi_7days",
    "create_order_roi",
    "convert_cost",
    "ecp_cpa_platform",
)

DEFAULT_ACCOUNT_REPORT_FIELDS: tuple[str, ...] = DEFAULT_ROI_REPORT_FIELDS

DEFAULT_MATERIAL_REPORT_FIELDS: tuple[str, ...] = (
    "material_id",
    "material_name",
    "stat_cost",
    "show_cnt",
    "click_cnt",
    "ctr",
    "pay_order_count",
    "pay_order_amount",
    "prepay_and_pay_order_roi",
    "all_order_pay_roi_7days",
)

RiskLevel = Literal["low", "medium", "high"]
DiagnosisStatus = Literal["scale", "cut", "pause_candidate", "watch", "insufficient_data"]
MaterialFatigueStatus = Literal["fatigue", "winner", "watch", "insufficient_data"]
AccountRole = Literal["scale", "test", "retarget", "hold"]
PolicyAction = Literal[
    "budget_update",
    "bid_update",
    "roi_goal_update",
    "ad_status_update",
    "campaign_create",
    "campaign_update",
    "ad_create",
    "ad_update",
    "material_bind",
    "material_upload",
    "carousel_create",
    "material_delete",
    "order_create",
    "control_task_create",
    "control_task_update",
    "uni_authorization_apply",
    "product_delete",
    "account_budget_update",
    "order_budget_update",
    "smart_boost_update",
    "raw_write",
]
MaterialDecisionStatus = Literal["winner", "fatigue", "test_candidate", "rejected", "watch"]


class DateRange(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_date_range(self) -> DateRange:
        if self.start_date > self.end_date:
            raise ValueError("start_date must be less than or equal to end_date.")
        return self


class HealthResponse(BaseModel):
    status: str
    has_direct_access_token: bool
    has_oauth_gateway: bool
    write_enabled: bool
    api_base_url: str
    outbound_http_trust_env: bool


class ReportQuery(DateRange):
    advertiser_id: int = Field(gt=0)
    marketing_goal: str = "ALL"
    order_platform: str = "QIANCHUAN"
    fields: list[str] = Field(default_factory=lambda: list(DEFAULT_ROI_REPORT_FIELDS))
    filtering: dict[str, Any] = Field(default_factory=dict)
    ad_ids: list[int] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    time_granularity: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=1000)


class AccountReportQuery(ReportQuery):
    fields: list[str] = Field(default_factory=lambda: list(DEFAULT_ACCOUNT_REPORT_FIELDS))


class GenericParamRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class RoiDiagnosisRequest(DateRange):
    advertiser_id: int = Field(gt=0)
    target_roi: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("100"))
    marketing_goal: str = "ALL"
    order_platform: str = "QIANCHUAN"
    fields: list[str] = Field(default_factory=lambda: list(DEFAULT_ROI_REPORT_FIELDS))
    filtering: dict[str, Any] = Field(default_factory=dict)
    ad_ids: list[int] = Field(default_factory=list)
    min_spend: Decimal | None = Field(default=None, ge=Decimal("0"))
    min_clicks: int | None = Field(default=None, ge=0)
    min_orders: int | None = Field(default=None, ge=0)
    max_ads: int = Field(default=100, ge=1, le=500)
    gross_margin_rate: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1"))
    refund_rate: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), lt=Decimal("1"))
    extra_cost_rate: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("1"))
    roi_safety_margin_rate: Decimal = Field(default=Decimal("0.1"), ge=Decimal("0"), le=Decimal("1"))

    @model_validator(mode="after")
    def validate_profit_inputs(self) -> RoiDiagnosisRequest:
        if self.gross_margin_rate is None:
            return self
        net_margin = self.gross_margin_rate * (Decimal("1") - self.refund_rate) - self.extra_cost_rate
        if net_margin <= 0:
            raise ValueError("gross_margin_rate/refund_rate/extra_cost_rate produce non-positive net margin.")
        return self


class SnapshotSyncRequest(DateRange):
    advertiser_id: int = Field(gt=0)
    marketing_goal: str = "ALL"
    order_platform: str = "QIANCHUAN"
    fields: list[str] = Field(default_factory=lambda: list(DEFAULT_ROI_REPORT_FIELDS))
    filtering: dict[str, Any] = Field(default_factory=dict)
    ad_ids: list[int] = Field(default_factory=list)
    page_size: int = Field(default=500, ge=1, le=500)
    max_pages_per_day: int = Field(default=10, ge=1, le=100)


class SnapshotSyncResponse(BaseModel):
    advertiser_id: int
    start_date: date
    end_date: date
    synced_dates: list[date]
    rows_upserted: int
    request_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AdDailySnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    advertiser_id: int
    biz_date: date
    ad_id: str
    ad_name: str | None = None
    marketing_goal: str | None = None
    order_platform: str | None = None
    stat_cost: Decimal
    show_cnt: int
    click_cnt: int
    pay_order_count: int
    pay_order_amount: Decimal
    roi: Decimal
    roi_metric: str | None = None
    conversion_cost: Decimal | None = None
    source_request_id: str | None = None


class ListResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int


class AdSnapshotListResponse(BaseModel):
    items: list[AdDailySnapshotRead]
    total: int
    page: int
    page_size: int


class AdRoiInsight(BaseModel):
    ad_id: str
    ad_name: str | None = None
    cost: Decimal
    roi: Decimal
    roi_metric: str
    target_roi: Decimal
    pay_order_count: int
    click_count: int
    pay_order_amount: Decimal | None = None
    conversion_cost: Decimal | None = None
    breakeven_roi: Decimal | None = None
    estimated_net_profit: Decimal | None = None
    profit_roi: Decimal | None = None
    status: DiagnosisStatus
    reasons: list[str] = Field(default_factory=list)


class ActionProposal(BaseModel):
    action_type: str
    object_type: str
    object_id: str | None = None
    title: str
    reason: str
    risk_level: RiskLevel
    requires_confirmation: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    guardrails: list[str] = Field(default_factory=list)


class RoiDiagnosisSummary(BaseModel):
    advertiser_id: int
    start_date: date
    end_date: date
    target_roi: Decimal
    total_cost: Decimal
    estimated_pay_order_amount: Decimal
    estimated_roi: Decimal
    breakeven_roi: Decimal | None = None
    estimated_net_profit: Decimal | None = None
    profit_roi: Decimal | None = None
    ad_count: int
    scale_count: int
    cut_count: int
    pause_candidate_count: int
    insufficient_data_count: int


class RoiDiagnosisResponse(BaseModel):
    summary: RoiDiagnosisSummary
    insights: list[AdRoiInsight]
    proposals: list[ActionProposal]
    source: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class RoiStrategyRequest(RoiDiagnosisRequest):
    persist_decision: bool = True


class RoiStrategyResponse(BaseModel):
    decision_no: str | None = None
    diagnosis: RoiDiagnosisResponse


class MaterialFatigueRequest(BaseModel):
    advertiser_id: int = Field(gt=0)
    current_start_date: date
    current_end_date: date
    previous_start_date: date
    previous_end_date: date
    marketing_goal: str = "ALL"
    order_platform: str = "QIANCHUAN"
    fields: list[str] = Field(default_factory=lambda: list(DEFAULT_MATERIAL_REPORT_FIELDS))
    filtering: dict[str, Any] = Field(default_factory=dict)
    ad_ids: list[int] = Field(default_factory=list)
    min_cost: Decimal = Field(default=Decimal("50"), ge=Decimal("0"))
    min_impressions: int = Field(default=1000, ge=0)
    ctr_drop_rate: Decimal = Field(default=Decimal("0.3"), ge=Decimal("0"), le=Decimal("1"))
    roi_drop_rate: Decimal = Field(default=Decimal("0.3"), ge=Decimal("0"), le=Decimal("1"))
    max_materials: int = Field(default=200, ge=1, le=500)
    page_size: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_ranges(self) -> MaterialFatigueRequest:
        if self.current_start_date > self.current_end_date:
            raise ValueError("current_start_date must be less than or equal to current_end_date.")
        if self.previous_start_date > self.previous_end_date:
            raise ValueError("previous_start_date must be less than or equal to previous_end_date.")
        return self


class MaterialFatigueInsight(BaseModel):
    material_id: str
    material_name: str | None = None
    current_cost: Decimal
    previous_cost: Decimal
    current_impressions: int
    previous_impressions: int
    current_clicks: int
    previous_clicks: int
    current_ctr: Decimal
    previous_ctr: Decimal
    current_roi: Decimal
    previous_roi: Decimal
    current_pay_order_amount: Decimal
    previous_pay_order_amount: Decimal
    status: MaterialFatigueStatus
    reasons: list[str] = Field(default_factory=list)
    recommended_action: str


class MaterialFatigueResponse(BaseModel):
    advertiser_id: int
    current_start_date: date
    current_end_date: date
    previous_start_date: date
    previous_end_date: date
    total_materials: int
    fatigue_count: int
    winner_count: int
    insufficient_data_count: int
    insights: list[MaterialFatigueInsight]
    source: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AccountPolicyUpsertRequest(BaseModel):
    account_name: str | None = Field(default=None, max_length=255)
    account_role: AccountRole = "hold"
    allowed_actions: list[PolicyAction] | None = None
    product_scope: dict[str, Any] = Field(default_factory=dict)
    allowed_ad_ids: list[str] = Field(default_factory=list)
    allowed_material_ids: list[str] = Field(default_factory=list)
    allowed_product_ids: list[str] = Field(default_factory=list)
    target_roi: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("100"))
    max_daily_budget_change_rate: Decimal = Field(default=Decimal("0.2"), ge=Decimal("0"), le=Decimal("1"))
    write_enabled: bool = False
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def normalize_whitelist_ids(self) -> AccountPolicyUpsertRequest:
        self.allowed_ad_ids = _dedupe_non_empty_strings(self.allowed_ad_ids)
        self.allowed_material_ids = _dedupe_non_empty_strings(self.allowed_material_ids)
        self.allowed_product_ids = _dedupe_non_empty_strings(self.allowed_product_ids)
        return self


class AccountPolicyRead(AccountPolicyUpsertRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int
    advertiser_id: int
    allowed_actions: list[PolicyAction]
    created_at: datetime
    updated_at: datetime


class AccountPolicyListResponse(BaseModel):
    items: list[AccountPolicyRead]
    total: int


class AutonomyProfileUpsertRequest(BaseModel):
    target_roi: Decimal = Field(gt=Decimal("0"), le=Decimal("100"))
    gross_margin_rate: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))
    refund_rate: Decimal = Field(ge=Decimal("0"), lt=Decimal("1"))
    extra_cost_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    max_budget: Decimal = Field(gt=Decimal("0"))
    max_daily_increase: Decimal = Field(gt=Decimal("0"))

    @model_validator(mode="after")
    def validate_profit_inputs(self) -> AutonomyProfileUpsertRequest:
        net_margin = self.gross_margin_rate * (Decimal("1") - self.refund_rate)
        net_margin -= self.extra_cost_rate
        if net_margin <= 0:
            raise ValueError(
                "gross_margin_rate/refund_rate/extra_cost_rate produce non-positive net margin."
            )
        return self


class AutonomyProfileRead(AutonomyProfileUpsertRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int
    advertiser_id: int
    locked: bool
    revision: int
    configured_by: str
    last_user_request: str | None = None
    created_at: datetime
    updated_at: datetime


class LaunchAuthorizationUpsertRequest(BaseModel):
    allowed_product_ids: list[str] = Field(min_length=1)
    allowed_aweme_ids: list[str] = Field(default_factory=list)
    allowed_material_ids: list[str] = Field(default_factory=list)
    allowed_marketing_goals: list[str] = Field(min_length=1)
    allowed_marketing_scenes: list[str] = Field(min_length=1)
    max_initial_campaign_budget: Decimal = Field(gt=Decimal("0"))
    max_initial_ad_budget: Decimal = Field(gt=Decimal("0"))
    min_account_balance: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    min_product_inventory: int = Field(default=1, ge=0)
    max_campaigns_per_day: int = Field(default=1, ge=1, le=100)
    max_ads_per_day: int = Field(default=1, ge=1, le=100)
    allow_campaign_create: bool = False
    allow_ad_create: bool = False

    @model_validator(mode="after")
    def normalize_launch_boundaries(self) -> LaunchAuthorizationUpsertRequest:
        self.allowed_product_ids = _dedupe_non_empty_strings(self.allowed_product_ids)
        self.allowed_aweme_ids = _dedupe_non_empty_strings(self.allowed_aweme_ids)
        self.allowed_material_ids = _dedupe_non_empty_strings(self.allowed_material_ids)
        self.allowed_marketing_goals = _dedupe_non_empty_strings(self.allowed_marketing_goals)
        self.allowed_marketing_scenes = _dedupe_non_empty_strings(self.allowed_marketing_scenes)
        if not self.allowed_product_ids:
            raise ValueError("allowed_product_ids must contain at least one product.")
        return self


class LaunchAuthorizationRead(LaunchAuthorizationUpsertRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int
    advertiser_id: int
    locked: bool
    revision: int
    configured_by: str
    last_user_request: str | None = None
    created_at: datetime
    updated_at: datetime


class CampaignPlaybookUpsertRequest(BaseModel):
    account_role: AccountRole
    objective: str = Field(min_length=1, max_length=128)
    target_roi: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("100"))
    min_orders: int = Field(default=1, ge=0)
    scale_roi_multiplier: Decimal = Field(default=Decimal("1.2"), gt=Decimal("1"), le=Decimal("10"))
    cut_roi_multiplier: Decimal = Field(default=Decimal("0.75"), ge=Decimal("0"), lt=Decimal("1"))
    max_budget_change_rate: Decimal = Field(default=Decimal("0.2"), ge=Decimal("0"), le=Decimal("1"))
    material_rules: dict[str, Any] = Field(default_factory=dict)
    rules: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class CampaignPlaybookRead(CampaignPlaybookUpsertRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int
    playbook_key: str
    created_at: datetime
    updated_at: datetime


class CampaignPlaybookListResponse(BaseModel):
    items: list[CampaignPlaybookRead]
    total: int


class MaterialDecisionCreateRequest(BaseModel):
    advertiser_id: int = Field(gt=0)
    material_id: str = Field(min_length=1, max_length=128)
    material_name: str | None = Field(default=None, max_length=255)
    status: MaterialDecisionStatus
    recommended_action: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=4, max_length=2000)
    source_json: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None


class MaterialDecisionRead(MaterialDecisionCreateRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int
    decision_no: str
    created_by: str
    created_at: datetime


class MaterialDecisionListResponse(BaseModel):
    items: list[MaterialDecisionRead]
    total: int
    page: int
    page_size: int


class StrategyReviewCreateRequest(BaseModel):
    advertiser_id: int | None = Field(default=None, gt=0)
    playbook_key: str | None = Field(default=None, max_length=64)
    period_start: date | None = None
    period_end: date | None = None
    summary: str = Field(min_length=8, max_length=5000)
    worked: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    suggested_changes: list[dict[str, Any]] = Field(default_factory=list)
    next_checks: list[str] = Field(default_factory=list)
    confidence: Decimal = Field(default=Decimal("0.5"), ge=Decimal("0"), le=Decimal("1"))
    source_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_period(self) -> StrategyReviewCreateRequest:
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("period_start must be less than or equal to period_end.")
        return self


class StrategyReviewRead(StrategyReviewCreateRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int
    review_no: str
    created_by: str
    created_at: datetime


class StrategyReviewListResponse(BaseModel):
    items: list[StrategyReviewRead]
    total: int
    page: int
    page_size: int


class BlockedProposal(BaseModel):
    proposal: ActionProposal
    reason: str


class DeliveryPlanRequest(RoiStrategyRequest):
    include_material_decisions: bool = True
    material_statuses: list[MaterialDecisionStatus] = Field(
        default_factory=lambda: ["winner", "test_candidate", "fatigue"]
    )
    playbook_key: str | None = Field(default=None, max_length=64)
    include_strategy_reviews: bool = True


class DeliveryPlanResponse(BaseModel):
    account_policy: AccountPolicyRead | None = None
    playbook: CampaignPlaybookRead | None = None
    strategy: RoiStrategyResponse
    allowed_actions: list[PolicyAction]
    approved_proposals: list[ActionProposal]
    blocked_proposals: list[BlockedProposal]
    material_decisions: list[MaterialDecisionRead]
    recent_strategy_reviews: list[StrategyReviewRead]
    warnings: list[str] = Field(default_factory=list)


class RoiDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    decision_no: str
    advertiser_id: int
    start_date: date
    end_date: date
    target_roi: Decimal
    total_cost: Decimal
    estimated_pay_order_amount: Decimal
    estimated_roi: Decimal
    ad_count: int
    proposal_count: int
    scale_count: int
    cut_count: int
    pause_candidate_count: int
    insufficient_data_count: int
    status: str
    created_by: str
    request_json: dict[str, Any]
    summary_json: dict[str, Any]
    insights_json: list[dict[str, Any]]
    proposals_json: list[dict[str, Any]]
    warnings_json: list[str]
    created_at: datetime


class RoiDecisionListResponse(BaseModel):
    items: list[RoiDecisionRead]
    total: int
    page: int
    page_size: int


class ToolSpecRead(BaseModel):
    key: str
    title: str
    method: Literal["GET", "POST"]
    path: str
    risk_level: RiskLevel
    write: bool
    docs_url: str
    capabilities: list[str]


class ToolListResponse(BaseModel):
    items: list[ToolSpecRead]
    total: int


class ToolCallRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False
    reason: str | None = Field(default=None, max_length=500)


class BudgetUpdateItem(BaseModel):
    ad_id: int = Field(gt=0)
    budget: Decimal = Field(gt=Decimal("0"))
    previous_budget: Decimal | None = Field(default=None, gt=Decimal("0"))


class BudgetUpdateRequest(BaseModel):
    advertiser_id: int = Field(gt=0)
    data: list[BudgetUpdateItem] = Field(min_length=1, max_length=10)
    confirm: bool = False
    reason: str | None = Field(default=None, max_length=500)


class BidUpdateItem(BaseModel):
    ad_id: int = Field(gt=0)
    bid: Decimal = Field(gt=Decimal("0"))


class BidUpdateRequest(BaseModel):
    advertiser_id: int = Field(gt=0)
    data: list[BidUpdateItem] = Field(min_length=1, max_length=10)
    confirm: bool = False
    reason: str | None = Field(default=None, max_length=500)


class RoiGoalUpdateItem(BaseModel):
    ad_id: int = Field(gt=0)
    target_roi: Decimal = Field(ge=Decimal("0.01"), le=Decimal("100"))


class RoiGoalUpdateRequest(BaseModel):
    advertiser_id: int = Field(gt=0)
    data: list[RoiGoalUpdateItem] = Field(min_length=1, max_length=1)
    confirm: bool = False
    reason: str | None = Field(default=None, max_length=500)


class AdStatusUpdateRequest(BaseModel):
    advertiser_id: int = Field(gt=0)
    ad_ids: list[int] = Field(min_length=1, max_length=10)
    operation: Literal["ENABLE", "DISABLE"]
    confirm: bool = False
    reason: str | None = Field(default=None, max_length=500)


def _dedupe_non_empty_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped
