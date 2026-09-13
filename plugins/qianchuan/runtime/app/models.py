from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db import Base


class AdDailySnapshot(Base):
    __tablename__ = "ad_daily_snapshot"
    __table_args__ = (
        UniqueConstraint("advertiser_id", "biz_date", "ad_id", name="uq_ad_daily_snapshot_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    advertiser_id: Mapped[int] = mapped_column(Integer, nullable=False)
    biz_date: Mapped[date] = mapped_column(Date, nullable=False)
    ad_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ad_name: Mapped[str | None] = mapped_column(String(255))
    marketing_goal: Mapped[str | None] = mapped_column(String(64))
    order_platform: Mapped[str | None] = mapped_column(String(64))
    stat_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    show_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    click_cnt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pay_order_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pay_order_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    roi: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    roi_metric: Mapped[str | None] = mapped_column(String(64))
    conversion_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    raw_row_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    source_request_id: Mapped[str | None] = mapped_column(String(64))
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RoiDecision(Base):
    __tablename__ = "roi_decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    advertiser_id: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_roi: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    estimated_pay_order_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    estimated_roi: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    ad_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    proposal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scale_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cut_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pause_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    insufficient_data_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    insights_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    proposals_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    warnings_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class AccountPolicy(Base):
    __tablename__ = "account_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    advertiser_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    account_name: Mapped[str | None] = mapped_column(String(255))
    account_role: Mapped[str] = mapped_column(String(32), nullable=False, default="hold")
    allowed_actions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    product_scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    target_roi: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    max_daily_budget_change_rate: Mapped[Decimal] = mapped_column(
        Numeric(8, 4),
        nullable=False,
        default=Decimal("0.2"),
    )
    write_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    @property
    def allowed_ad_ids(self) -> list[str]:
        value = self.product_scope.get("allowed_ad_ids", [])
        return [str(item) for item in value] if isinstance(value, list) else []

    @property
    def allowed_material_ids(self) -> list[str]:
        value = self.product_scope.get("allowed_material_ids", [])
        return [str(item) for item in value] if isinstance(value, list) else []

    @property
    def allowed_product_ids(self) -> list[str]:
        value = self.product_scope.get("allowed_product_ids", [])
        return [str(item) for item in value] if isinstance(value, list) else []


class AutonomyProfile(Base):
    __tablename__ = "autonomy_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    advertiser_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    target_roi: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    gross_margin_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    refund_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    extra_cost_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    max_budget: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    max_daily_increase: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    configured_by: Mapped[str] = mapped_column(String(64), nullable=False)
    last_user_request: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class LaunchAuthorization(Base):
    """User-approved, locked boundaries for agent-created Qianchuan launches."""

    __tablename__ = "launch_authorization"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    advertiser_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    allowed_product_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_aweme_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_material_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_marketing_goals: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_marketing_scenes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    max_initial_campaign_budget: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    max_initial_ad_budget: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    min_account_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    min_product_inventory: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_campaigns_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_ads_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    allow_campaign_create: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_ad_create: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    configured_by: Mapped[str] = mapped_column(String(64), nullable=False)
    last_user_request: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CampaignPlaybook(Base):
    __tablename__ = "campaign_playbook"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playbook_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    account_role: Mapped[str] = mapped_column(String(32), nullable=False)
    objective: Mapped[str] = mapped_column(String(128), nullable=False)
    target_roi: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    min_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scale_roi_multiplier: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal("1.2"))
    cut_roi_multiplier: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal("0.75"))
    max_budget_change_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False, default=Decimal("0.2"))
    material_rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MaterialDecision(Base):
    __tablename__ = "material_decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    advertiser_id: Mapped[int] = mapped_column(Integer, nullable=False)
    material_id: Mapped[str] = mapped_column(String(128), nullable=False)
    material_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    recommended_action: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class StrategyReview(Base):
    __tablename__ = "strategy_review"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    advertiser_id: Mapped[int | None] = mapped_column(Integer)
    playbook_key: Mapped[str | None] = mapped_column(String(64))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    worked: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    failed: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    suggested_changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    next_checks: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0.5"))
    source_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class DeliveryStart(Base):
    __tablename__ = "delivery_start"
    ad_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)


class DeadlineHeartbeat(Base):
    __tablename__ = "deadline_heartbeat"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checked_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class DeliveryDeadline(Base):
    __tablename__ = "delivery_deadline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ad_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    end_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="scheduled", nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class ActionAuditLog(Base):
    __tablename__ = "action_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operator_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_code: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
