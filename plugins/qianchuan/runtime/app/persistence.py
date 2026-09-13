from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models import (
    AccountPolicy,
    ActionAuditLog,
    AdDailySnapshot,
    AutonomyProfile,
    CampaignPlaybook,
    LaunchAuthorization,
    MaterialDecision,
    RoiDecision,
    StrategyReview,
)
from app.orchestration import (
    default_allowed_actions,
    enforce_account_policy_for_write,
    filter_proposals_by_policy,
    material_id_allowed,
    normalize_allowed_actions,
    payload_advertiser_id,
    payload_material_ids,
    payload_object_ids,
    payload_product_ids,
)
from app.launch import enforce_launch_authorization
from app.schemas import (
    AccountPolicyRead,
    AccountPolicyUpsertRequest,
    AdDailySnapshotRead,
    AdSnapshotListResponse,
    AutonomyProfileRead,
    AutonomyProfileUpsertRequest,
    CampaignPlaybookRead,
    CampaignPlaybookUpsertRequest,
    LaunchAuthorizationRead,
    LaunchAuthorizationUpsertRequest,
    DeliveryPlanRequest,
    DeliveryPlanResponse,
    MaterialDecisionCreateRequest,
    MaterialDecisionRead,
    ReportQuery,
    RoiDecisionRead,
    RoiDiagnosisRequest,
    RoiStrategyRequest,
    RoiStrategyResponse,
    SnapshotSyncRequest,
    SnapshotSyncResponse,
    StrategyReviewCreateRequest,
    StrategyReviewRead,
)
from app.strategy import QianchuanStrategyService, ValidationError

ROI_KEYS = (
    "all_order_pay_roi_7days",
    "prepay_and_pay_order_roi",
    "create_order_roi",
    "pay_order_roi",
    "roi",
)
COST_KEYS = ("stat_cost", "cost", "consume")
SHOW_KEYS = ("show_cnt", "show_count", "impression")
CLICK_KEYS = ("click_cnt", "click_count", "click")
ORDER_KEYS = ("pay_order_count", "create_order_count", "order_count")
PAY_AMOUNT_KEYS = ("pay_order_amount", "total_pay_order_amount", "order_pay_amount")
CONVERSION_COST_KEYS = ("convert_cost", "ecp_cpa_platform", "conversion_cost")


class DecisionNotFoundError(LookupError):
    pass


class PersistenceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def sync_snapshots(
        self,
        request: SnapshotSyncRequest,
        *,
        strategy_service: QianchuanStrategyService,
        operator_id: str,
    ) -> SnapshotSyncResponse:
        rows_upserted = 0
        synced_dates: list[date] = []
        request_ids: list[str] = []
        warnings: list[str] = []
        for biz_date in _iter_dates(request.start_date, request.end_date):
            date_rows = 0
            for page in range(1, request.max_pages_per_day + 1):
                payload = strategy_service.get_ad_report(_sync_query(request, biz_date=biz_date, page=page))
                request_id = _optional_str(payload.get("request_id"))
                if request_id:
                    request_ids.append(request_id)
                rows = _extract_rows(payload)
                for row in rows:
                    self._upsert_snapshot(request, biz_date=biz_date, row=row, request_id=request_id)
                rows_upserted += len(rows)
                date_rows += len(rows)
                if len(rows) < request.page_size:
                    break
            synced_dates.append(biz_date)
            if date_rows == 0:
                warnings.append(f"No rows returned for {biz_date.isoformat()}.")
        self.write_audit(
            operator_id=operator_id,
            action_code="snapshot.sync",
            target_type="ad_snapshot",
            target_id=str(request.advertiser_id),
            request_payload=request.model_dump(mode="json"),
            response_payload={"rows_upserted": rows_upserted, "warnings": warnings},
            commit=False,
        )
        self.db.commit()
        return SnapshotSyncResponse(
            advertiser_id=request.advertiser_id,
            start_date=request.start_date,
            end_date=request.end_date,
            synced_dates=synced_dates,
            rows_upserted=rows_upserted,
            request_ids=sorted(set(request_ids)),
            warnings=warnings,
        )

    def list_snapshots(
        self,
        *,
        advertiser_id: int,
        date_from: date,
        date_to: date,
        ad_ids: list[str] | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AdDailySnapshot], int]:
        statement: Select[tuple[AdDailySnapshot]] = select(AdDailySnapshot).where(
            AdDailySnapshot.advertiser_id == advertiser_id,
            AdDailySnapshot.biz_date >= date_from,
            AdDailySnapshot.biz_date <= date_to,
        )
        count_statement = select(func.count()).select_from(AdDailySnapshot).where(
            AdDailySnapshot.advertiser_id == advertiser_id,
            AdDailySnapshot.biz_date >= date_from,
            AdDailySnapshot.biz_date <= date_to,
        )
        if ad_ids:
            statement = statement.where(AdDailySnapshot.ad_id.in_(ad_ids))
            count_statement = count_statement.where(AdDailySnapshot.ad_id.in_(ad_ids))
        statement = statement.order_by(
            AdDailySnapshot.biz_date.desc(),
            AdDailySnapshot.stat_cost.desc(),
            AdDailySnapshot.ad_id.asc(),
        )
        return self.db.scalars(statement.offset(offset).limit(limit)).all(), int(
            self.db.scalar(count_statement) or 0
        )

    def snapshot_response(
        self,
        *,
        items: list[AdDailySnapshot],
        total: int,
        page: int,
        page_size: int,
    ) -> AdSnapshotListResponse:
        return AdSnapshotListResponse(
            items=[AdDailySnapshotRead.model_validate(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    def build_roi_strategy(
        self,
        request: RoiStrategyRequest,
        *,
        strategy_service: QianchuanStrategyService,
        operator_id: str,
    ) -> RoiStrategyResponse:
        rows = self._aggregate_rows(request)
        diagnosis = strategy_service.diagnose_roi_from_rows(
            rows,
            RoiDiagnosisRequest.model_validate(request.model_dump()),
            source={"source": "local_ad_daily_snapshot"},
        )
        decision_no: str | None = None
        if request.persist_decision:
            decision = self._create_decision(request, diagnosis=diagnosis, operator_id=operator_id)
            decision_no = decision.decision_no
            diagnosis.source["decision_no"] = decision_no
            self.write_audit(
                operator_id=operator_id,
                action_code="strategy.roi_decide",
                target_type="roi_decision",
                target_id=decision_no,
                request_payload=request.model_dump(mode="json"),
                response_payload=diagnosis.model_dump(mode="json"),
                commit=False,
            )
            self.db.commit()
            self.db.refresh(decision)
        return RoiStrategyResponse(decision_no=decision_no, diagnosis=diagnosis)

    def build_delivery_plan(
        self,
        request: DeliveryPlanRequest,
        *,
        strategy_service: QianchuanStrategyService,
        operator_id: str,
        require_policy: bool,
    ) -> DeliveryPlanResponse:
        policy = self.get_account_policy(request.advertiser_id)
        playbook = self.get_campaign_playbook(request.playbook_key) if request.playbook_key else None
        strategy_request = RoiStrategyRequest.model_validate(request.model_dump())
        if policy is not None and request.target_roi is None and policy.target_roi is not None:
            strategy_request.target_roi = policy.target_roi
        if playbook is not None and strategy_request.target_roi is None and playbook.target_roi is not None:
            strategy_request.target_roi = playbook.target_roi
        if playbook is not None and request.min_orders is None:
            strategy_request.min_orders = playbook.min_orders
        strategy = self.build_roi_strategy(
            strategy_request,
            strategy_service=strategy_service,
            operator_id=operator_id,
        )
        approved, blocked, warnings = filter_proposals_by_policy(
            policy=policy,
            proposals=strategy.diagnosis.proposals,
            require_policy=require_policy,
        )
        material_decisions: list[MaterialDecisionRead] = []
        if request.include_material_decisions:
            materials, _ = self.list_material_decisions(
                advertiser_id=request.advertiser_id,
                statuses=request.material_statuses,
                limit=100,
                offset=0,
            )
            if policy is not None:
                materials = [item for item in materials if material_id_allowed(policy, item.material_id)]
            material_decisions = [serialize_material_decision(item) for item in materials]
        strategy_reviews: list[StrategyReviewRead] = []
        if request.include_strategy_reviews:
            reviews, _ = self.list_strategy_reviews(
                advertiser_id=request.advertiser_id,
                playbook_key=request.playbook_key,
                limit=5,
                offset=0,
            )
            strategy_reviews = [serialize_strategy_review(item) for item in reviews]
        if policy is None and require_policy:
            warnings.append("Account policy is required for AI write actions, but this advertiser has no policy.")
        if playbook is None and request.playbook_key:
            warnings.append(f"Playbook {request.playbook_key} was not found.")
        return DeliveryPlanResponse(
            account_policy=serialize_account_policy(policy) if policy else None,
            playbook=serialize_campaign_playbook(playbook) if playbook else None,
            strategy=strategy,
            allowed_actions=policy.allowed_actions if policy else default_allowed_actions("hold"),
            approved_proposals=approved,
            blocked_proposals=blocked,
            material_decisions=material_decisions,
            recent_strategy_reviews=strategy_reviews,
            warnings=warnings,
        )

    def list_decisions(
        self,
        *,
        advertiser_id: int | None,
        limit: int,
        offset: int,
    ) -> tuple[list[RoiDecision], int]:
        statement: Select[tuple[RoiDecision]] = select(RoiDecision)
        count_statement = select(func.count()).select_from(RoiDecision)
        if advertiser_id is not None:
            statement = statement.where(RoiDecision.advertiser_id == advertiser_id)
            count_statement = count_statement.where(RoiDecision.advertiser_id == advertiser_id)
        statement = statement.order_by(RoiDecision.created_at.desc(), RoiDecision.id.desc())
        return self.db.scalars(statement.offset(offset).limit(limit)).all(), int(
            self.db.scalar(count_statement) or 0
        )

    def get_decision(self, decision_no: str) -> RoiDecision:
        decision = self.db.scalar(select(RoiDecision).where(RoiDecision.decision_no == decision_no))
        if decision is None:
            raise DecisionNotFoundError(decision_no)
        return decision

    def write_audit(
        self,
        *,
        operator_id: str,
        action_code: str,
        target_type: str,
        target_id: str,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
        commit: bool = True,
        error_message: str | None = None,
    ) -> None:
        self.db.add(
            ActionAuditLog(
                operator_id=operator_id,
                action_code=action_code,
                target_type=target_type,
                target_id=target_id,
                request_json=_jsonable(request_payload),
                response_json=_jsonable(response_payload),
                request_id=_optional_str(response_payload.get("request_id")),
                error_message=error_message,
            )
        )
        if commit:
            self.db.commit()

    def upsert_account_policy(
        self,
        advertiser_id: int,
        request: AccountPolicyUpsertRequest,
    ) -> AccountPolicy:
        policy = self.db.scalar(select(AccountPolicy).where(AccountPolicy.advertiser_id == advertiser_id))
        if policy is None:
            policy = AccountPolicy(advertiser_id=advertiser_id)
            self.db.add(policy)
        policy.account_name = request.account_name
        policy.account_role = request.account_role
        policy.allowed_actions = normalize_allowed_actions(request.account_role, request.allowed_actions)
        policy.product_scope = {
            **request.product_scope,
            "allowed_ad_ids": request.allowed_ad_ids,
            "allowed_material_ids": request.allowed_material_ids,
            "allowed_product_ids": request.allowed_product_ids,
        }
        policy.target_roi = request.target_roi
        policy.max_daily_budget_change_rate = request.max_daily_budget_change_rate
        policy.write_enabled = request.write_enabled
        policy.notes = request.notes
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def get_autonomy_profile(self, advertiser_id: int) -> AutonomyProfile | None:
        return self.db.scalar(
            select(AutonomyProfile).where(AutonomyProfile.advertiser_id == advertiser_id)
        )

    def get_launch_authorization(self, advertiser_id: int) -> LaunchAuthorization | None:
        return self.db.scalar(
            select(LaunchAuthorization).where(
                LaunchAuthorization.advertiser_id == advertiser_id
            )
        )

    def save_launch_authorization(
        self,
        advertiser_id: int,
        request: LaunchAuthorizationUpsertRequest,
        *,
        operator_id: str,
        user_requested_update: bool = False,
        user_request_summary: str | None = None,
    ) -> LaunchAuthorization:
        authorization = self.get_launch_authorization(advertiser_id)
        action = "launch.authorization.create"
        if authorization is None:
            authorization = LaunchAuthorization(
                advertiser_id=advertiser_id,
                configured_by=operator_id,
                revision=1,
            )
            self.db.add(authorization)
        else:
            if not user_requested_update:
                raise ValidationError(
                    "Launch authorization is locked. Only update it after the user explicitly "
                    "asks to change the launch boundaries."
                )
            summary = (user_request_summary or "").strip()
            if len(summary) < 8:
                raise ValidationError(
                    "Updating launch authorization requires a user_request_summary of at least "
                    "8 characters."
                )
            action = "launch.authorization.update"
            authorization.revision += 1

        for field, value in request.model_dump().items():
            setattr(authorization, field, value)
        authorization.locked = True
        authorization.configured_by = operator_id
        authorization.last_user_request = (
            user_request_summary or "initial explicit user launch boundaries"
        ).strip()
        self.write_audit(
            operator_id=operator_id,
            action_code=action,
            target_type="launch_authorization",
            target_id=str(advertiser_id),
            request_payload={
                **request.model_dump(mode="json"),
                "user_requested_update": user_requested_update,
                "user_request_summary": user_request_summary,
            },
            response_payload={"locked": True, "revision": authorization.revision},
            commit=False,
        )
        self.db.commit()
        self.db.refresh(authorization)
        return authorization

    def save_autonomy_profile(
        self,
        advertiser_id: int,
        request: AutonomyProfileUpsertRequest,
        *,
        operator_id: str,
        user_requested_update: bool = False,
        user_request_summary: str | None = None,
    ) -> AutonomyProfile:
        profile = self.get_autonomy_profile(advertiser_id)
        action = "autonomy.profile.create"
        if profile is None:
            profile = AutonomyProfile(
                advertiser_id=advertiser_id,
                configured_by=operator_id,
                revision=1,
            )
            self.db.add(profile)
        else:
            if not user_requested_update:
                raise ValidationError(
                    "Autonomy profile is locked. Reuse the stored values. Only update it after "
                    "the user explicitly asks to change them."
                )
            summary = (user_request_summary or "").strip()
            if len(summary) < 8:
                raise ValidationError(
                    "Updating a locked autonomy profile requires a user_request_summary of at "
                    "least 8 characters."
                )
            action = "autonomy.profile.update"
            profile.revision += 1

        profile.target_roi = request.target_roi
        profile.gross_margin_rate = request.gross_margin_rate
        profile.refund_rate = request.refund_rate
        profile.extra_cost_rate = request.extra_cost_rate
        profile.max_budget = request.max_budget
        profile.max_daily_increase = request.max_daily_increase
        profile.locked = True
        profile.configured_by = operator_id
        profile.last_user_request = (user_request_summary or "initial explicit user values").strip()
        self.write_audit(
            operator_id=operator_id,
            action_code=action,
            target_type="autonomy_profile",
            target_id=str(advertiser_id),
            request_payload={
                **request.model_dump(mode="json"),
                "user_requested_update": user_requested_update,
                "user_request_summary": user_request_summary,
            },
            response_payload={"locked": True, "revision": profile.revision},
            commit=False,
        )
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def get_account_policy(self, advertiser_id: int) -> AccountPolicy | None:
        return self.db.scalar(select(AccountPolicy).where(AccountPolicy.advertiser_id == advertiser_id))

    def list_account_policies(self) -> list[AccountPolicy]:
        return self.db.scalars(select(AccountPolicy).order_by(AccountPolicy.advertiser_id.asc())).all()

    def upsert_campaign_playbook(
        self,
        playbook_key: str,
        request: CampaignPlaybookUpsertRequest,
    ) -> CampaignPlaybook:
        playbook = self.db.scalar(select(CampaignPlaybook).where(CampaignPlaybook.playbook_key == playbook_key))
        if playbook is None:
            playbook = CampaignPlaybook(playbook_key=playbook_key)
            self.db.add(playbook)
        playbook.account_role = request.account_role
        playbook.objective = request.objective
        playbook.target_roi = request.target_roi
        playbook.min_orders = request.min_orders
        playbook.scale_roi_multiplier = request.scale_roi_multiplier
        playbook.cut_roi_multiplier = request.cut_roi_multiplier
        playbook.max_budget_change_rate = request.max_budget_change_rate
        playbook.material_rules = request.material_rules
        playbook.rules = request.rules
        playbook.enabled = request.enabled
        self.db.commit()
        self.db.refresh(playbook)
        return playbook

    def get_campaign_playbook(self, playbook_key: str) -> CampaignPlaybook | None:
        return self.db.scalar(select(CampaignPlaybook).where(CampaignPlaybook.playbook_key == playbook_key))

    def list_campaign_playbooks(self) -> list[CampaignPlaybook]:
        return self.db.scalars(
            select(CampaignPlaybook).order_by(CampaignPlaybook.account_role.asc(), CampaignPlaybook.playbook_key.asc())
        ).all()

    def create_material_decision(
        self,
        request: MaterialDecisionCreateRequest,
        *,
        operator_id: str,
    ) -> MaterialDecision:
        decision = MaterialDecision(
            decision_no=f"qcmd_{uuid4().hex[:20]}",
            advertiser_id=request.advertiser_id,
            material_id=request.material_id,
            material_name=request.material_name,
            status=request.status,
            recommended_action=request.recommended_action,
            reason=request.reason,
            source_json=request.source_json,
            created_by=operator_id,
            expires_at=request.expires_at,
        )
        self.db.add(decision)
        self.db.commit()
        self.db.refresh(decision)
        return decision

    def list_material_decisions(
        self,
        *,
        advertiser_id: int | None,
        statuses: list[str] | None,
        limit: int,
        offset: int,
    ) -> tuple[list[MaterialDecision], int]:
        statement: Select[tuple[MaterialDecision]] = select(MaterialDecision)
        count_statement = select(func.count()).select_from(MaterialDecision)
        if advertiser_id is not None:
            statement = statement.where(MaterialDecision.advertiser_id == advertiser_id)
            count_statement = count_statement.where(MaterialDecision.advertiser_id == advertiser_id)
        if statuses:
            statement = statement.where(MaterialDecision.status.in_(statuses))
            count_statement = count_statement.where(MaterialDecision.status.in_(statuses))
        statement = statement.order_by(MaterialDecision.created_at.desc(), MaterialDecision.id.desc())
        return self.db.scalars(statement.offset(offset).limit(limit)).all(), int(
            self.db.scalar(count_statement) or 0
        )

    def create_strategy_review(
        self,
        request: StrategyReviewCreateRequest,
        *,
        operator_id: str,
    ) -> StrategyReview:
        review = StrategyReview(
            review_no=f"qcsr_{uuid4().hex[:20]}",
            advertiser_id=request.advertiser_id,
            playbook_key=request.playbook_key,
            period_start=request.period_start,
            period_end=request.period_end,
            summary=request.summary,
            worked=request.worked,
            failed=request.failed,
            suggested_changes=request.suggested_changes,
            next_checks=request.next_checks,
            confidence=request.confidence,
            source_json=request.source_json,
            created_by=operator_id,
        )
        self.db.add(review)
        self.db.commit()
        self.db.refresh(review)
        return review

    def list_strategy_reviews(
        self,
        *,
        advertiser_id: int | None,
        playbook_key: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[StrategyReview], int]:
        statement: Select[tuple[StrategyReview]] = select(StrategyReview)
        count_statement = select(func.count()).select_from(StrategyReview)
        if advertiser_id is not None:
            statement = statement.where(StrategyReview.advertiser_id == advertiser_id)
            count_statement = count_statement.where(StrategyReview.advertiser_id == advertiser_id)
        if playbook_key:
            statement = statement.where(StrategyReview.playbook_key == playbook_key)
            count_statement = count_statement.where(StrategyReview.playbook_key == playbook_key)
        statement = statement.order_by(StrategyReview.created_at.desc(), StrategyReview.id.desc())
        return self.db.scalars(statement.offset(offset).limit(limit)).all(), int(
            self.db.scalar(count_statement) or 0
        )

    def ensure_account_policy_allows(
        self,
        action_code: str,
        payload,
        *,
        require_policy: bool,
    ) -> None:
        from app.deadline import enforce_deadline
        enforce_deadline(self.db, action_code, payload)
        advertiser_id = payload_advertiser_id(payload)
        policy = self.get_account_policy(advertiser_id) if advertiser_id is not None else None
        enforce_account_policy_for_write(
            policy=policy,
            action_code=action_code,
            payload=payload,
            require_policy=require_policy,
        )

    def ensure_launch_authorization_allows(
        self,
        action_code: str,
        payload: dict[str, Any],
        *,
        product_inventory: dict[str, int] | None = None,
        product_channels: dict[str, dict[str, Any]] | None = None,
        product_card_image_ids: dict[str, set[str]] | None = None,
        account_valid_balance: Decimal | None = None,
        preflight_complete: bool = False,
    ) -> None:
        if action_code not in {
            "tool.qianchuan_campaign_create_v1",
            "tool.qianchuan_ad_create_v1",
            "tool.qianchuan_uni_aweme_ad_create_v1",
        }:
            return
        advertiser_id = payload_advertiser_id(payload)
        if advertiser_id is None:
            raise ValidationError("Launch payload must include advertiser_id.")
        start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        successful_actions = int(
            self.db.scalar(
                select(func.count())
                .select_from(ActionAuditLog)
                .where(
                    ActionAuditLog.action_code == action_code,
                    ActionAuditLog.target_id == str(advertiser_id),
                    ActionAuditLog.error_message.is_(None),
                    ActionAuditLog.created_at >= start_of_day,
                )
            )
            or 0
        )
        enforce_launch_authorization(
            authorization=self.get_launch_authorization(advertiser_id),
            action_code=action_code,
            payload=payload,
            successful_actions_today=successful_actions,
            product_inventory=product_inventory,
            product_channels=product_channels,
            product_card_image_ids=product_card_image_ids,
            account_valid_balance=account_valid_balance,
            preflight_complete=preflight_complete,
        )

    def ensure_action_cooldown(self, action_code: str, payload, cooldown_minutes: int) -> None:
        if cooldown_minutes <= 0:
            return
        # A safety stop must never be blocked by the cooldown created by an earlier
        # ENABLE call. Budget/ROI increases and subsequent ENABLE calls still cool down.
        if _is_reversible_safety_stop(action_code, payload):
            return
        advertiser_id = payload_advertiser_id(payload)
        if advertiser_id is None:
            raise ValidationError("Write payload must include advertiser_id for cooldown enforcement.")
        object_ids = _cooldown_scope_ids(action_code, payload)
        cutoff = datetime.now(UTC) - timedelta(minutes=cooldown_minutes)
        logs = self.db.scalars(
            select(ActionAuditLog).where(
                ActionAuditLog.action_code == action_code,
                ActionAuditLog.target_id == str(advertiser_id),
                ActionAuditLog.error_message.is_(None),
                ActionAuditLog.created_at >= cutoff,
            )
        ).all()
        conflicts: set[str] = set()
        for log in logs:
            log_ids = _cooldown_scope_ids(action_code, log.request_json)
            if not object_ids or not log_ids:
                conflicts.add(str(advertiser_id))
                continue
            conflicts.update(object_ids.intersection(log_ids))
        if conflicts:
            joined = ", ".join(sorted(conflicts))
            raise ValidationError(
                f"{action_code} is cooling down for {joined}. "
                f"Wait {cooldown_minutes} minutes between repeated write actions."
            )

    def ensure_autonomous_budget_limits(
        self,
        payload,
        *,
        advertiser_id: str,
        min_budget: Decimal,
        max_budget: Decimal,
        max_increase_rate: Decimal,
        max_decrease_rate: Decimal,
        max_actions_per_day: int,
        max_daily_increase: Decimal,
    ) -> None:
        payload_advertiser = payload_advertiser_id(payload)
        if str(payload_advertiser) != advertiser_id:
            raise ValidationError(
                f"Autonomous budget boundary only permits advertiser {advertiser_id}."
            )
        items = list(getattr(payload, "data", []))
        if not items:
            raise ValidationError("Autonomous budget update requires at least one item.")

        proposed_increase = Decimal("0")
        for item in items:
            previous = item.previous_budget
            if previous is None or previous <= 0:
                raise ValidationError(
                    f"Autonomous budget update for ad {item.ad_id} requires previous_budget."
                )
            budget = Decimal(str(item.budget))
            previous_budget = Decimal(str(previous))
            if budget < min_budget or budget > max_budget:
                raise ValidationError(
                    f"Autonomous budget {budget} for ad {item.ad_id} is outside hard bounds "
                    f"{min_budget}..{max_budget}."
                )
            delta = budget - previous_budget
            rate = abs(delta) / previous_budget
            limit = max_increase_rate if delta > 0 else max_decrease_rate
            if rate > limit:
                raise ValidationError(
                    f"Autonomous budget update for ad {item.ad_id} changes {rate:.2%}, "
                    f"above hard limit {limit:.2%}."
                )
            proposed_increase += max(delta, Decimal("0"))

        cutoff = datetime.now(UTC) - timedelta(hours=24)
        logs = self.db.scalars(
            select(ActionAuditLog).where(
                ActionAuditLog.action_code == "ad_budget.update",
                ActionAuditLog.target_id == advertiser_id,
                ActionAuditLog.error_message.is_(None),
                ActionAuditLog.created_at >= cutoff,
            )
        ).all()
        existing_actions = 0
        existing_increase = Decimal("0")
        for log in logs:
            log_items = log.request_json.get("data", [])
            if not isinstance(log_items, list):
                continue
            existing_actions += len(log_items)
            for item in log_items:
                if not isinstance(item, dict):
                    continue
                try:
                    budget = Decimal(str(item["budget"]))
                    previous = Decimal(str(item["previous_budget"]))
                except (KeyError, InvalidOperation, TypeError, ValueError):
                    continue
                existing_increase += max(budget - previous, Decimal("0"))

        if existing_actions + len(items) > max_actions_per_day:
            raise ValidationError(
                "Autonomous rolling 24-hour action limit exceeded: "
                f"{existing_actions}+{len(items)}>{max_actions_per_day}."
            )
        if existing_increase + proposed_increase > max_daily_increase:
            raise ValidationError(
                "Autonomous rolling 24-hour budget increase limit exceeded: "
                f"{existing_increase}+{proposed_increase}>{max_daily_increase}."
            )

    def _aggregate_rows(self, request: RoiStrategyRequest) -> list[dict[str, Any]]:
        snapshots, _ = self.list_snapshots(
            advertiser_id=request.advertiser_id,
            date_from=request.start_date,
            date_to=request.end_date,
            ad_ids=[str(item) for item in request.ad_ids] if request.ad_ids else None,
            limit=request.max_ads * 366,
            offset=0,
        )
        grouped: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "ad_id": "",
                "ad_name": None,
                "stat_cost": Decimal("0"),
                "show_cnt": 0,
                "click_cnt": 0,
                "pay_order_count": 0,
                "pay_order_amount": Decimal("0"),
            }
        )
        for item in snapshots:
            bucket = grouped[item.ad_id]
            bucket["ad_id"] = item.ad_id
            bucket["ad_name"] = item.ad_name or bucket["ad_name"]
            bucket["stat_cost"] += item.stat_cost
            bucket["show_cnt"] += item.show_cnt
            bucket["click_cnt"] += item.click_cnt
            bucket["pay_order_count"] += item.pay_order_count
            bucket["pay_order_amount"] += item.pay_order_amount
        rows = list(grouped.values())
        rows.sort(key=lambda item: item["stat_cost"], reverse=True)
        return [_jsonable(row) for row in rows[: request.max_ads]]

    def _create_decision(self, request: RoiStrategyRequest, *, diagnosis, operator_id: str) -> RoiDecision:
        summary = diagnosis.summary
        decision = RoiDecision(
            decision_no=f"qcrd_{uuid4().hex[:20]}",
            advertiser_id=request.advertiser_id,
            start_date=request.start_date,
            end_date=request.end_date,
            target_roi=summary.target_roi,
            total_cost=summary.total_cost,
            estimated_pay_order_amount=summary.estimated_pay_order_amount,
            estimated_roi=summary.estimated_roi,
            ad_count=summary.ad_count,
            proposal_count=len(diagnosis.proposals),
            scale_count=summary.scale_count,
            cut_count=summary.cut_count,
            pause_candidate_count=summary.pause_candidate_count,
            insufficient_data_count=summary.insufficient_data_count,
            status="proposed",
            created_by=operator_id,
            request_json=request.model_dump(mode="json"),
            summary_json=summary.model_dump(mode="json"),
            insights_json=[item.model_dump(mode="json") for item in diagnosis.insights],
            proposals_json=[item.model_dump(mode="json") for item in diagnosis.proposals],
            warnings_json=list(diagnosis.warnings),
        )
        self.db.add(decision)
        return decision

    def _upsert_snapshot(
        self,
        request: SnapshotSyncRequest,
        *,
        biz_date: date,
        row: dict[str, Any],
        request_id: str | None,
    ) -> None:
        flattened = _flatten_row(row)
        ad_id = _ad_id_from_row(flattened)
        snapshot = self.db.scalar(
            select(AdDailySnapshot).where(
                AdDailySnapshot.advertiser_id == request.advertiser_id,
                AdDailySnapshot.biz_date == biz_date,
                AdDailySnapshot.ad_id == ad_id,
            )
        )
        if snapshot is None:
            snapshot = AdDailySnapshot(advertiser_id=request.advertiser_id, biz_date=biz_date, ad_id=ad_id)
            self.db.add(snapshot)
        cost = _first_decimal(flattened, COST_KEYS) or Decimal("0")
        roi_metric, roi = _first_decimal_with_key(flattened, ROI_KEYS)
        pay_amount = _first_decimal(flattened, PAY_AMOUNT_KEYS)
        if pay_amount is None and roi > 0:
            pay_amount = cost * roi
        if roi == 0 and pay_amount is not None and cost > 0:
            roi = pay_amount / cost
            roi_metric = "computed_pay_amount_roi"
        snapshot.ad_name = _optional_str(flattened.get("ad_name") or flattened.get("name"))
        snapshot.marketing_goal = request.marketing_goal
        snapshot.order_platform = request.order_platform
        snapshot.stat_cost = cost
        snapshot.show_cnt = _first_int(flattened, SHOW_KEYS)
        snapshot.click_cnt = _first_int(flattened, CLICK_KEYS)
        snapshot.pay_order_count = _first_int(flattened, ORDER_KEYS)
        snapshot.pay_order_amount = pay_amount or Decimal("0")
        snapshot.roi = roi
        snapshot.roi_metric = roi_metric if roi_metric != "missing" else None
        snapshot.conversion_cost = _first_decimal(flattened, CONVERSION_COST_KEYS)
        snapshot.raw_row_json = _jsonable(row)
        snapshot.source_request_id = request_id


def serialize_decision(decision: RoiDecision) -> RoiDecisionRead:
    return RoiDecisionRead.model_validate(decision)


def serialize_account_policy(policy: AccountPolicy) -> AccountPolicyRead:
    return AccountPolicyRead.model_validate(policy)


def serialize_autonomy_profile(profile: AutonomyProfile) -> AutonomyProfileRead:
    return AutonomyProfileRead.model_validate(profile)


def serialize_launch_authorization(
    authorization: LaunchAuthorization,
) -> LaunchAuthorizationRead:
    return LaunchAuthorizationRead.model_validate(authorization)


def serialize_campaign_playbook(playbook: CampaignPlaybook) -> CampaignPlaybookRead:
    return CampaignPlaybookRead.model_validate(playbook)


def serialize_material_decision(decision: MaterialDecision) -> MaterialDecisionRead:
    return MaterialDecisionRead.model_validate(decision)


def serialize_strategy_review(review: StrategyReview) -> StrategyReviewRead:
    return StrategyReviewRead.model_validate(review)


_REVERSIBLE_STATUS_ACTIONS = {
    "ad_status.update",
    "tool.qianchuan_ad_status_update_v1",
    "tool.qianchuan_batch_campaign_status_update_v1",
    "tool.qianchuan_uni_promotion_ad_status_update_v1",
    "tool.qianchuan_uni_promotion_ad_control_task_status_update_v1",
    "tool.qianchuan_uni_promotion_ad_control_task_smart_control_status_update_v1",
}


def _is_reversible_safety_stop(action_code: str, payload) -> bool:
    if action_code not in _REVERSIBLE_STATUS_ACTIONS:
        return False
    if isinstance(payload, dict):
        value = payload.get("opt_status") or payload.get("operation") or payload.get("status")
    else:
        value = (
            getattr(payload, "opt_status", None)
            or getattr(payload, "operation", None)
            or getattr(payload, "status", None)
        )
    return str(value or "").upper() == "DISABLE"


def _sync_query(request: SnapshotSyncRequest, *, biz_date: date, page: int) -> ReportQuery:
    return ReportQuery(
        advertiser_id=request.advertiser_id,
        start_date=biz_date,
        end_date=biz_date,
        marketing_goal=request.marketing_goal,
        order_platform=request.order_platform,
        fields=request.fields,
        filtering=request.filtering,
        ad_ids=request.ad_ids,
        page=page,
        page_size=request.page_size,
    )


def _iter_dates(start_date: date, end_date: date) -> Iterable[date]:
    cursor = start_date
    while cursor <= end_date:
        yield cursor
        cursor += timedelta(days=1)


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


def _ad_id_from_row(row: dict[str, Any]) -> str:
    value = row.get("ad_id") or row.get("id") or row.get("adgroup_id")
    if value is None or str(value).strip() == "":
        raise ValueError("Qianchuan ad report row is missing ad_id.")
    return str(value)


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


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _payload_object_ids(payload) -> set[str]:
    if hasattr(payload, "data"):
        return {
            str(item.ad_id)
            for item in payload.data
            if getattr(item, "ad_id", None) is not None
        }
    if hasattr(payload, "ad_ids"):
        return {str(item) for item in payload.ad_ids}
    return set()


def _request_object_ids(request_json: dict[str, Any]) -> set[str]:
    return payload_object_ids(request_json)


_CREATE_ACTION_CODES = {
    "tool.qianchuan_campaign_create_v1",
    "tool.qianchuan_ad_create_v1",
    "tool.qianchuan_uni_aweme_ad_create_v1",
}


def _cooldown_scope_ids(action_code: str, payload) -> set[str]:
    """Scope a create cooldown to products when the new object has no official ad ID yet.

    New full-domain plans do not have an ad_id until after the official write succeeds.
    Treating an empty ID set as account-wide prevents a user-authorized, bounded
    multi-product/plan launch from creating its second plan. Product IDs preserve the
    no-repeat protection for the same creation scope while allowing different, explicitly
    allowlisted products to be created as separate plans.
    """
    if action_code in {"tool.qianchuan_file_image_ad_v2", "tool.qianchuan_file_video_ad_v2"}:
        if isinstance(payload, dict):
            signature = payload.get("image_signature") or payload.get("video_signature")
            if signature not in (None, ""):
                return {f"signature:{signature}"}
    if action_code == "tool.qianchuan_carousel_create_v2":
        image_ids = payload_material_ids(payload)
        if image_ids:
            return {f"image:{image_id}" for image_id in image_ids}
    object_ids = payload_object_ids(payload)
    if object_ids or action_code not in _CREATE_ACTION_CODES:
        return object_ids
    return {f"product:{product_id}" for product_id in payload_product_ids(payload)}
