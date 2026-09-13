from __future__ import annotations

import argparse
import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import build_engine, create_session_factory, init_db
from app.gateway import resolve_access_token
from app.persistence import PersistenceService
from app.qianchuan_client import QianchuanClient, QianchuanConfig
from app.schemas import BudgetUpdateRequest, RoiStrategyRequest, SnapshotSyncRequest
from app.strategy import QianchuanStrategyService, ValidationError


LOG = logging.getLogger("qianchuan.autonomous")


@dataclass
class AutonomousActionResult:
    ad_id: str
    action: str
    previous_budget: str | None = None
    budget: str | None = None
    status: str = "skipped"
    reason: str | None = None
    response: dict[str, Any] = field(default_factory=dict)


@dataclass
class AutonomousRunResult:
    status: str
    dry_run: bool
    advertiser_id: str
    start_date: str | None = None
    end_date: str | None = None
    decision_no: str | None = None
    rows_upserted: int = 0
    proposal_count: int = 0
    actions: list[AutonomousActionResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    required_user_inputs: list[dict[str, str]] = field(default_factory=list)


class AutonomousBudgetController:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Session,
        strategy_service: QianchuanStrategyService | None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.persistence = PersistenceService(db)
        self.strategy = strategy_service

    def run_once(self, *, today: date | None = None) -> AutonomousRunResult:
        self.settings.validate_autonomous_settings()
        advertiser_id = str(self.settings.qianchuan_autonomous_advertiser_id or "")
        result = AutonomousRunResult(
            status="running",
            dry_run=self.settings.qianchuan_autonomous_dry_run,
            advertiser_id=advertiser_id,
        )
        if not self.settings.qianchuan_autonomous_enabled:
            raise ValidationError("QIANCHUAN_AUTONOMOUS_ENABLED=false.")
        profile = self.persistence.get_autonomy_profile(int(advertiser_id))
        if profile is None:
            result.status = "setup_required"
            result.warnings.append(
                "Autonomy profile is missing. The agent must ask the user for every required "
                "value and save them before retrying. Do not infer or invent values."
            )
            result.required_user_inputs = autonomy_profile_questions()
            self._audit_run(result)
            return result
        if profile.max_budget <= self.settings.qianchuan_autonomous_min_budget:
            raise ValidationError(
                "Stored max_budget must be greater than QIANCHUAN_AUTONOMOUS_MIN_BUDGET."
            )
        strategy = self._strategy_service()
        if self._kill_switch_active():
            result.status = "stopped"
            result.warnings.append("Kill switch file exists; no API reads or writes were performed.")
            self._audit_run(result)
            return result

        window_end = (today or date.today()) - timedelta(days=1)
        window_start = window_end - timedelta(
            days=self.settings.qianchuan_autonomous_lookback_days - 1
        )
        result.start_date = window_start.isoformat()
        result.end_date = window_end.isoformat()

        try:
            sync = self.persistence.sync_snapshots(
                SnapshotSyncRequest(
                    advertiser_id=int(advertiser_id),
                    start_date=window_start,
                    end_date=window_end,
                ),
                strategy_service=strategy,
                operator_id=self.settings.qianchuan_autonomous_operator_id,
            )
            result.rows_upserted = sync.rows_upserted
            result.warnings.extend(sync.warnings)
            if sync.rows_upserted <= 0:
                result.status = "no_data"
                self._audit_run(result)
                return result

            decision = self.persistence.build_roi_strategy(
                RoiStrategyRequest(
                    advertiser_id=int(advertiser_id),
                    start_date=window_start,
                    end_date=window_end,
                    target_roi=profile.target_roi,
                    gross_margin_rate=profile.gross_margin_rate,
                    refund_rate=profile.refund_rate,
                    extra_cost_rate=profile.extra_cost_rate,
                    roi_safety_margin_rate=(
                        self.settings.qianchuan_autonomous_roi_safety_margin_rate
                    ),
                ),
                strategy_service=strategy,
                operator_id=self.settings.qianchuan_autonomous_operator_id,
            )
            result.decision_no = decision.decision_no
            proposals = [
                item
                for item in decision.diagnosis.proposals
                if item.action_type in {"increase_budget", "decrease_budget"}
            ]
            result.proposal_count = len(proposals)
            for proposal in proposals[: self.settings.qianchuan_autonomous_max_actions_per_run]:
                result.actions.append(self._apply_proposal(proposal.action_type, proposal.object_id))
            result.status = "dry_run" if self.settings.qianchuan_autonomous_dry_run else "completed"
        except Exception as exc:
            result.status = "failed"
            result.warnings.append(str(exc))
            self._audit_run(result, error_message=str(exc))
            raise

        self._audit_run(result)
        return result

    def _apply_proposal(self, action: str, ad_id: str | None) -> AutonomousActionResult:
        if not ad_id or not ad_id.isdigit():
            return AutonomousActionResult(
                ad_id=str(ad_id or ""),
                action=action,
                reason="Proposal has no numeric ad_id.",
            )
        detail = self._strategy_service().get_ad_detail(
            {
                "advertiser_id": int(self.settings.qianchuan_autonomous_advertiser_id or "0"),
                "ad_id": int(ad_id),
            }
        )
        previous = extract_ad_budget(detail, ad_id)
        if previous is None:
            return AutonomousActionResult(
                ad_id=ad_id,
                action=action,
                reason="Official ad detail response did not contain a verifiable current budget.",
            )
        budget = calculate_next_budget(
            previous,
            action=action,
            increase_rate=self.settings.qianchuan_autonomous_increase_rate,
            decrease_rate=self.settings.qianchuan_autonomous_decrease_rate,
            min_budget=self.settings.qianchuan_autonomous_min_budget,
            max_budget=self._profile().max_budget,
        )
        item_result = AutonomousActionResult(
            ad_id=ad_id,
            action=action,
            previous_budget=str(previous),
            budget=str(budget),
        )
        if budget == previous:
            item_result.reason = "Hard budget bounds leave no permitted change."
            return item_result
        if abs(budget - previous) < Decimal("100"):
            item_result.reason = "Permitted change is below the platform-safe minimum delta of 100."
            return item_result

        request = BudgetUpdateRequest(
            advertiser_id=int(self.settings.qianchuan_autonomous_advertiser_id or "0"),
            data=[{"ad_id": int(ad_id), "budget": budget, "previous_budget": previous}],
            confirm=True,
            reason=(
                f"autonomous ROI guardrail: {action}; current budget verified from official detail API"
            ),
        )
        if self.settings.qianchuan_autonomous_dry_run:
            item_result.status = "proposed"
            item_result.reason = "Dry-run; no write API was called."
            return item_result
        if self._kill_switch_active():
            item_result.reason = "Kill switch became active before write."
            return item_result

        self.persistence.ensure_account_policy_allows(
            "ad_budget.update",
            request,
            require_policy=self.settings.qianchuan_require_account_policy_for_writes,
        )
        self.persistence.ensure_action_cooldown(
            "ad_budget.update",
            request,
            self.settings.qianchuan_write_cooldown_minutes,
        )
        profile = self._profile()
        self.persistence.ensure_autonomous_budget_limits(
            request,
            advertiser_id=str(self.settings.qianchuan_autonomous_advertiser_id),
            min_budget=self.settings.qianchuan_autonomous_min_budget,
            max_budget=profile.max_budget,
            max_increase_rate=self.settings.qianchuan_autonomous_increase_rate,
            max_decrease_rate=self.settings.qianchuan_autonomous_decrease_rate,
            max_actions_per_day=self.settings.qianchuan_autonomous_max_actions_per_day,
            max_daily_increase=profile.max_daily_increase,
        )
        try:
            response = self._strategy_service().apply_budget_updates(request)
        except Exception as exc:
            self.persistence.write_audit(
                operator_id=self.settings.qianchuan_autonomous_operator_id,
                action_code="ad_budget.update",
                target_type="qianchuan_ad",
                target_id=str(request.advertiser_id),
                request_payload=request.model_dump(mode="json"),
                response_payload={},
                error_message=str(exc),
            )
            raise
        self.persistence.write_audit(
            operator_id=self.settings.qianchuan_autonomous_operator_id,
            action_code="ad_budget.update",
            target_type="qianchuan_ad",
            target_id=str(request.advertiser_id),
            request_payload=request.model_dump(mode="json"),
            response_payload=response,
        )
        item_result.status = "applied"
        item_result.response = response
        return item_result

    def _kill_switch_active(self) -> bool:
        return Path(self.settings.qianchuan_autonomous_kill_switch_file).expanduser().exists()

    def _profile(self):
        advertiser_id = int(self.settings.qianchuan_autonomous_advertiser_id or "0")
        profile = self.persistence.get_autonomy_profile(advertiser_id)
        if profile is None:
            raise ValidationError("Autonomy profile disappeared before action; failing closed.")
        return profile

    def _strategy_service(self) -> QianchuanStrategyService:
        if self.strategy is None:
            raise ValidationError("Strategy service is unavailable after autonomy setup.")
        return self.strategy

    def _audit_run(self, result: AutonomousRunResult, *, error_message: str | None = None) -> None:
        self.persistence.write_audit(
            operator_id=self.settings.qianchuan_autonomous_operator_id,
            action_code="autonomous.budget.run",
            target_type="qianchuan_account",
            target_id=result.advertiser_id,
            request_payload={
                "dry_run": result.dry_run,
                "start_date": result.start_date,
                "end_date": result.end_date,
            },
            response_payload=asdict(result),
            error_message=error_message,
        )


def calculate_next_budget(
    previous_budget: Decimal,
    *,
    action: str,
    increase_rate: Decimal,
    decrease_rate: Decimal,
    min_budget: Decimal,
    max_budget: Decimal | None,
) -> Decimal:
    if previous_budget <= 0:
        raise ValidationError("Current budget must be positive.")
    if action == "increase_budget":
        candidate = previous_budget * (Decimal("1") + increase_rate)
    elif action == "decrease_budget":
        candidate = previous_budget * (Decimal("1") - decrease_rate)
    else:
        raise ValidationError(f"Unsupported autonomous action: {action}")
    candidate = candidate.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    candidate = max(candidate, min_budget)
    if max_budget is not None:
        candidate = min(candidate, max_budget)
    return candidate


def autonomy_profile_questions() -> list[dict[str, str]]:
    return [
        {"field": "target_roi", "question": "目标 ROI 是多少？"},
        {"field": "gross_margin_rate", "question": "商品毛利率是多少（0 到 1）？"},
        {"field": "refund_rate", "question": "退款率是多少（0 到 1）？"},
        {"field": "extra_cost_rate", "question": "其他成本率是多少（0 到 1）？"},
        {"field": "max_budget", "question": "单计划预算绝对最高允许多少元？"},
        {
            "field": "max_daily_increase",
            "question": "滚动 24 小时内累计最多允许增加多少元预算？",
        },
    ]


def extract_ad_budget(payload: dict[str, Any], ad_id: str) -> Decimal | None:
    matches: list[Decimal] = []
    fallback: list[Decimal] = []
    for item in _walk_dicts(payload):
        budget = _decimal_or_none(item.get("budget"))
        if budget is None or budget <= 0:
            continue
        fallback.append(budget)
        if str(item.get("ad_id", "")) == str(ad_id):
            matches.append(budget)
    if len(set(matches)) == 1:
        return matches[0]
    if not matches and len(set(fallback)) == 1:
        return fallback[0]
    return None


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def build_strategy_service(settings: Settings) -> QianchuanStrategyService:
    return QianchuanStrategyService(
        client=QianchuanClient(
            QianchuanConfig(
                api_base_url=settings.qianchuan_api_base_url,
                timeout_seconds=settings.qianchuan_request_timeout_seconds,
                allowed_advertiser_ids=frozenset(settings.qianchuan_allowed_advertiser_ids),
                trust_env=settings.outbound_http_trust_env,
            )
        ),
        access_token=resolve_access_token(settings),
        write_enabled=settings.qianchuan_write_enabled,
        default_target_roi=settings.qianchuan_default_target_roi,
        default_min_spend=settings.qianchuan_default_min_spend,
        default_min_clicks=settings.qianchuan_default_min_clicks,
        default_min_orders=settings.qianchuan_default_min_orders,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Guarded autonomous Qianchuan budget controller")
    parser.add_argument("--loop", action="store_true", help="Run continuously instead of once")
    parser.add_argument("--interval-minutes", type=int, default=60)
    args = parser.parse_args()
    if args.interval_minutes < 5:
        parser.error("--interval-minutes must be at least 5")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    settings.validate_for_startup()
    engine = build_engine(settings)
    init_db(engine)
    session_factory = create_session_factory(engine)
    stop_event = threading.Event()
    failed = False
    try:
        while True:
            with session_factory() as db:
                controller = AutonomousBudgetController(
                    settings=settings,
                    db=db,
                    strategy_service=(
                        build_strategy_service(settings)
                        if PersistenceService(db).get_autonomy_profile(
                            int(settings.qianchuan_autonomous_advertiser_id or "0")
                        )
                        is not None
                        else None
                    ),
                )
                try:
                    result = controller.run_once()
                    LOG.info("%s", json.dumps(asdict(result), ensure_ascii=False, default=str))
                except Exception:
                    failed = True
                    LOG.exception("Autonomous budget run failed closed")
            if not args.loop:
                break
            stop_event.wait(args.interval_minutes * 60)
    except KeyboardInterrupt:
        LOG.info("Stopped by operator")
    finally:
        engine.dispose()
    if failed and not args.loop:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
