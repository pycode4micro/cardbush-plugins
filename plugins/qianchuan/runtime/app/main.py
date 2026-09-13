from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.auth import require_local_api_key
from app.config import Settings, get_settings
from app.db import build_engine, create_session_factory, init_db
from app.gateway import OAuthGatewayClient, TokenResolutionError, resolve_access_token
from app.launch import (
    extract_account_valid_balance,
    extract_product_channels,
    extract_product_card_image_ids,
    extract_product_inventory,
    extract_required_aweme_id,
    extract_uni_aweme_account,
    enforce_uni_aweme_create_eligibility,
)
from app.orchestration import payload_advertiser_id, payload_product_ids
from app.persistence import DecisionNotFoundError, PersistenceService, serialize_decision
from app.persistence import (
    serialize_account_policy,
    serialize_campaign_playbook,
    serialize_material_decision,
    serialize_strategy_review,
)
from app.qianchuan_client import QianchuanApiError, QianchuanClient, QianchuanConfig
from app.schemas import (
    AccountReportQuery,
    AccountPolicyListResponse,
    AccountPolicyRead,
    AccountPolicyUpsertRequest,
    AdSnapshotListResponse,
    AdStatusUpdateRequest,
    BidUpdateRequest,
    BudgetUpdateRequest,
    CampaignPlaybookListResponse,
    CampaignPlaybookRead,
    CampaignPlaybookUpsertRequest,
    DeliveryPlanRequest,
    DeliveryPlanResponse,
    GenericParamRequest,
    HealthResponse,
    MaterialDecisionCreateRequest,
    MaterialDecisionListResponse,
    MaterialDecisionRead,
    MaterialFatigueRequest,
    MaterialFatigueResponse,
    ReportQuery,
    RoiDecisionListResponse,
    RoiDecisionRead,
    RoiDiagnosisRequest,
    RoiDiagnosisResponse,
    RoiGoalUpdateRequest,
    RoiStrategyRequest,
    RoiStrategyResponse,
    SnapshotSyncRequest,
    SnapshotSyncResponse,
    StrategyReviewCreateRequest,
    StrategyReviewListResponse,
    StrategyReviewRead,
    ToolCallRequest,
    ToolListResponse,
    ToolSpecRead,
)
from app.strategy import (
    ConfirmationRequiredError,
    QianchuanStrategyService,
    StrategyError,
    ValidationError,
    WriteDisabledError,
)
from app.tools import get_tool, list_tools


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_for_startup()
    engine = build_engine(settings)
    init_db(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    from app.deadline import run_once
    async def deadline_loop():
        while True:
            try:
                await asyncio.to_thread(run_once, settings, engine, app.state.session_factory)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Deadline worker cycle failed")
            await asyncio.sleep(15)
    deadline_task = asyncio.create_task(deadline_loop())
    try:
        yield
    finally:
        deadline_task.cancel()
        try:
            await deadline_task
        except asyncio.CancelledError:
            pass
        engine.dispose()


app = FastAPI(title="Qianchuan Tool Service", version=__version__, lifespan=lifespan)


def get_runtime_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db_session(request: Request):
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_operator_id(
    request: Request,
    settings: Settings = Depends(get_runtime_settings),
) -> str:
    return require_local_api_key(request, settings)


def build_strategy_service(settings: Settings) -> QianchuanStrategyService:
    try:
        access_token = resolve_access_token(settings)
    except TokenResolutionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return QianchuanStrategyService(
        client=QianchuanClient(
            QianchuanConfig(
                api_base_url=settings.qianchuan_api_base_url,
                timeout_seconds=settings.qianchuan_request_timeout_seconds,
                allowed_advertiser_ids=frozenset(settings.qianchuan_allowed_advertiser_ids),
                trust_env=settings.outbound_http_trust_env,
            )
        ),
        access_token=access_token,
        write_enabled=settings.qianchuan_write_enabled,
        default_target_roi=Decimal(str(settings.qianchuan_default_target_roi)),
        default_min_spend=Decimal(str(settings.qianchuan_default_min_spend)),
        default_min_clicks=settings.qianchuan_default_min_clicks,
        default_min_orders=settings.qianchuan_default_min_orders,
    )


@app.get("/healthz", response_model=HealthResponse)
def healthz(settings: Settings = Depends(get_runtime_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        has_direct_access_token=bool(settings.qianchuan_access_token),
        has_oauth_gateway=bool(settings.oauth_gateway_base_url and settings.oauth_gateway_hmac_secret),
        write_enabled=settings.qianchuan_write_enabled,
        api_base_url=settings.qianchuan_api_base_url,
        outbound_http_trust_env=settings.outbound_http_trust_env,
    )


@app.get("/readyz")
def readyz(request: Request) -> dict[str, str]:
    engine: Engine = request.app.state.engine
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database is not ready") from exc
    return {"status": "ok", "database": "ok"}


@app.get("/api/tools", response_model=ToolListResponse)
def api_tools(_: str = Depends(get_operator_id)) -> ToolListResponse:
    items = [ToolSpecRead.model_validate(item) for item in list_tools()]
    return ToolListResponse(items=items, total=len(items))


@app.post("/api/oauth/start-url")
def oauth_start_url(
    payload: dict[str, Any],
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    try:
        return OAuthGatewayClient(settings).create_start_url(payload)
    except TokenResolutionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/oauth/authorizations")
def oauth_authorizations(
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    try:
        return OAuthGatewayClient(settings).list_authorizations()
    except TokenResolutionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/tools/{tool_key}/call")
def call_tool(
    tool_key: str,
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    service = build_strategy_service(settings)
    tool_payload = payload.payload or payload.params
    tool_spec = get_tool(tool_key)
    try:
        persistence = PersistenceService(db)
        if tool_spec is not None and tool_spec.write:
            if not tool_payload:
                raise ValidationError("Write tool payload must include advertiser_id.")
            persistence.ensure_account_policy_allows(
                f"tool.{tool_key}",
                tool_payload,
                require_policy=settings.qianchuan_require_account_policy_for_writes,
            )
            persistence.ensure_action_cooldown(
                f"tool.{tool_key}",
                tool_payload,
                settings.qianchuan_write_cooldown_minutes,
            )
        response = service.call_tool(tool_key, payload)
    except (QianchuanApiError, StrategyError) as exc:
        raise _to_http_exception(exc) from exc
    PersistenceService(db).write_audit(
        operator_id=operator_id,
        action_code=f"tool.{tool_key}",
        target_type="qianchuan_tool",
        target_id=tool_key,
        request_payload=payload.model_dump(mode="json"),
        response_payload=response,
    )
    return response


@app.get("/api/accounts")
def accounts(
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    try:
        if not settings.qianchuan_access_token:
            return OAuthGatewayClient(settings).get_resolved_advertisers()
        return build_strategy_service(settings).get_authorized_accounts()
    except (QianchuanApiError, TokenResolutionError) as exc:
        raise _to_http_exception(exc) from exc


@app.post("/api/reports/account")
def account_report(payload: AccountReportQuery, settings: Settings = Depends(get_runtime_settings), _: str = Depends(get_operator_id)):
    return _call_read(lambda service: service.get_account_report(payload), settings)


@app.post("/api/reports/ad")
def ad_report(payload: ReportQuery, settings: Settings = Depends(get_runtime_settings), _: str = Depends(get_operator_id)):
    return _call_read(lambda service: service.get_ad_report(payload), settings)


@app.post("/api/reports/material")
def material_report(payload: ReportQuery, settings: Settings = Depends(get_runtime_settings), _: str = Depends(get_operator_id)):
    return _call_read(lambda service: service.get_material_report(payload), settings)


@app.post("/api/reports/search-words")
def search_word_report(payload: ReportQuery, settings: Settings = Depends(get_runtime_settings), _: str = Depends(get_operator_id)):
    return _call_read(lambda service: service.get_search_word_report(payload), settings)


@app.post("/api/reports/uni-promotion")
def uni_promotion_report(payload: ReportQuery, settings: Settings = Depends(get_runtime_settings), _: str = Depends(get_operator_id)):
    return _call_read(lambda service: service.get_uni_promotion_report(payload), settings)


@app.post("/api/suggestions/roi-goal")
def suggest_roi_goal(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.suggest_roi_goal(payload.params), settings)


@app.post("/api/suggestions/budget")
def suggest_budget(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.suggest_budget(payload.params), settings)


@app.post("/api/estimate/effect")
def estimate_effect(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.estimate_effect(payload.params), settings)


@app.post("/api/launch/campaigns")
def create_campaign(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_campaign_create_v1",
        payload,
        operator_id,
        db,
        settings,
        "create_campaign",
    )


@app.post("/api/launch/campaigns/update")
def update_campaign(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_campaign_update_v1",
        payload,
        operator_id,
        db,
        settings,
        "update_campaign",
    )


@app.post("/api/launch/campaigns/query")
def get_campaigns(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_campaigns(payload.params), settings)


@app.post("/api/launch/ads")
def create_ad(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_ad_create_v1",
        payload,
        operator_id,
        db,
        settings,
        "create_ad",
    )


@app.post("/api/launch/ads/update")
def update_ad(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_ad_update_v1",
        payload,
        operator_id,
        db,
        settings,
        "update_ad",
    )


@app.post("/api/launch/ads/query")
def get_ads(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_ads(payload.params), settings)


@app.post("/api/launch/ads/detail")
def get_ad_detail(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_ad_detail(payload.params), settings)


@app.post("/api/launch/uni-aweme/ads")
def create_uni_aweme_ad(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_uni_aweme_ad_create_v1",
        payload,
        operator_id,
        db,
        settings,
        "create_uni_aweme_ad",
    )


@app.post("/api/launch/uni-aweme/ads/update")
def update_uni_aweme_ad(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_uni_aweme_ad_update_v1",
        payload,
        operator_id,
        db,
        settings,
        "update_uni_aweme_ad",
    )


@app.post("/api/launch/uni-promotion/ads/upgrade-multiplier")
def upgrade_uni_promotion_to_multiplier(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_uni_promotion_multiplier_upgrade_v1",
        payload,
        operator_id,
        db,
        settings,
        "upgrade_uni_promotion_to_multiplier",
    )


@app.post("/api/launch/uni-promotion/ads/query")
def get_uni_promotions(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_uni_promotions(payload.params), settings)


@app.post("/api/launch/uni-promotion/ads/detail")
def get_uni_promotion_ad_detail(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_uni_promotion_ad_detail(payload.params), settings)


@app.post("/api/launch/uni-promotion/products/query")
def get_uni_promotion_products(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_uni_promotion_products(payload.params), settings)


@app.post("/api/launch/uni-promotion/ads/budget")
def update_uni_promotion_ad_budget(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_uni_promotion_ad_budget_update_v1",
        payload,
        operator_id,
        db,
        settings,
        "update_uni_promotion_ad_budget",
    )


@app.post("/api/launch/uni-promotion/ads/roi2-goal")
def update_uni_promotion_ad_roi2_goal(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_uni_promotion_ad_roi2_goal_update_v1",
        payload,
        operator_id,
        db,
        settings,
        "update_uni_promotion_ad_roi2_goal",
    )


@app.post("/api/launch/uni-promotion/ads/status")
def update_uni_promotion_ad_status(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_uni_promotion_ad_status_update_v1",
        payload,
        operator_id,
        db,
        settings,
        "update_uni_promotion_ad_status",
    )


@app.post("/api/launch/aweme/orders")
def create_aweme_order(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_aweme_order_create_v1",
        payload,
        operator_id,
        db,
        settings,
        "create_aweme_order",
    )


@app.post("/api/launch/aweme/uni-promotion/orders")
def create_aweme_uni_promotion_order(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_aweme_uni_promotion_order_create_v1",
        payload,
        operator_id,
        db,
        settings,
        "create_aweme_uni_promotion_order",
    )


@app.post("/api/launch/aweme/uni-promotion/ad-materials/query")
def get_aweme_uni_promotion_ad_materials(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_aweme_uni_promotion_ad_materials(payload.params), settings)


@app.post("/api/launch/uni-promotion/control-tasks")
def create_uni_promotion_control_task(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_uni_promotion_ad_control_task_create_v1",
        payload,
        operator_id,
        db,
        settings,
        "create_uni_promotion_control_task",
    )


@app.post("/api/launch/uni-promotion/smart-control-tasks")
def create_uni_promotion_smart_control_task(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_uni_promotion_ad_control_task_smart_control_create_v1",
        payload,
        operator_id,
        db,
        settings,
        "create_uni_promotion_smart_control_task",
    )


@app.post("/api/launch/ad-materials/query")
def get_ad_materials(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_ad_materials(payload.params), settings)


@app.post("/api/launch/ad-materials/delete")
def delete_ad_material(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_ad_material_delete_v1",
        payload,
        operator_id,
        db,
        settings,
        "delete_ad_material",
    )


@app.post("/api/launch/materials/query")
def get_materials(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_materials(payload.params), settings)


@app.post("/api/launch/material-ads/query")
def get_material_ads(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_material_ads(payload.params), settings)


@app.post("/api/launch/images/query")
def get_images(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_images(payload.params), settings)


@app.post("/api/launch/uni-promotion/ad-materials/add")
def add_uni_promotion_ad_material(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_uni_promotion_ad_material_add_v1",
        payload,
        operator_id,
        db,
        settings,
        "add_uni_promotion_ad_material",
    )


@app.post("/api/launch/uni-promotion/ad-materials/delete")
def delete_uni_promotion_ad_material(
    payload: ToolCallRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_tool_action(
        "tool.qianchuan_uni_promotion_ad_material_delete_v1",
        payload,
        operator_id,
        db,
        settings,
        "delete_uni_promotion_ad_material",
    )


@app.post("/api/launch/uni-promotion/ad-materials/query")
def get_uni_promotion_ad_materials(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_uni_promotion_ad_materials(payload.params), settings)


@app.post("/api/launch/uni-promotion/ad-products/query")
def get_uni_promotion_ad_products(
    payload: GenericParamRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> dict[str, Any]:
    return _call_read(lambda service: service.get_uni_promotion_ad_products(payload.params), settings)


@app.post("/api/diagnose/roi", response_model=RoiDiagnosisResponse)
def diagnose_roi(
    payload: RoiDiagnosisRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> RoiDiagnosisResponse:
    try:
        return build_strategy_service(settings).diagnose_roi(payload)
    except (QianchuanApiError, StrategyError) as exc:
        raise _to_http_exception(exc) from exc


@app.post("/api/diagnose/material-fatigue", response_model=MaterialFatigueResponse)
def diagnose_material_fatigue(
    payload: MaterialFatigueRequest,
    settings: Settings = Depends(get_runtime_settings),
    _: str = Depends(get_operator_id),
) -> MaterialFatigueResponse:
    try:
        return build_strategy_service(settings).analyze_material_fatigue(payload)
    except (QianchuanApiError, StrategyError) as exc:
        raise _to_http_exception(exc) from exc


@app.post("/api/snapshots/ad/sync", response_model=SnapshotSyncResponse)
def sync_snapshots(
    payload: SnapshotSyncRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> SnapshotSyncResponse:
    try:
        return PersistenceService(db).sync_snapshots(
            payload,
            strategy_service=build_strategy_service(settings),
            operator_id=operator_id,
        )
    except (QianchuanApiError, StrategyError) as exc:
        raise _to_http_exception(exc) from exc


@app.get("/api/snapshots/ad", response_model=AdSnapshotListResponse)
def list_snapshots(
    advertiser_id: int = Query(..., gt=0),
    date_from: date = Query(...),
    date_to: date = Query(...),
    ad_ids: list[str] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    _: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> AdSnapshotListResponse:
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be less than or equal to date_to.")
    service = PersistenceService(db)
    items, total = service.list_snapshots(
        advertiser_id=advertiser_id,
        date_from=date_from,
        date_to=date_to,
        ad_ids=ad_ids,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return service.snapshot_response(items=items, total=total, page=page, page_size=page_size)


@app.post("/api/strategy/roi", response_model=RoiStrategyResponse)
def build_roi_strategy(
    payload: RoiStrategyRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> RoiStrategyResponse:
    return PersistenceService(db).build_roi_strategy(
        payload,
        strategy_service=build_strategy_service(settings),
        operator_id=operator_id,
    )


@app.get("/api/strategy/roi-decisions", response_model=RoiDecisionListResponse)
def list_decisions(
    advertiser_id: int | None = Query(default=None, gt=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    _: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> RoiDecisionListResponse:
    service = PersistenceService(db)
    items, total = service.list_decisions(
        advertiser_id=advertiser_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return RoiDecisionListResponse(
        items=[serialize_decision(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@app.get("/api/strategy/roi-decisions/{decision_no}", response_model=RoiDecisionRead)
def get_decision(
    decision_no: str,
    _: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> RoiDecisionRead:
    try:
        return serialize_decision(PersistenceService(db).get_decision(decision_no))
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Decision {decision_no} was not found.") from exc


@app.put("/api/orchestration/account-policies/{advertiser_id}", response_model=AccountPolicyRead)
def upsert_account_policy(
    advertiser_id: int,
    payload: AccountPolicyUpsertRequest,
    _: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> AccountPolicyRead:
    return serialize_account_policy(PersistenceService(db).upsert_account_policy(advertiser_id, payload))


@app.get("/api/orchestration/account-policies/{advertiser_id}", response_model=AccountPolicyRead)
def get_account_policy(
    advertiser_id: int,
    _: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> AccountPolicyRead:
    policy = PersistenceService(db).get_account_policy(advertiser_id)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Account policy for {advertiser_id} was not found.")
    return serialize_account_policy(policy)


@app.get("/api/orchestration/account-policies", response_model=AccountPolicyListResponse)
def list_account_policies(
    _: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> AccountPolicyListResponse:
    items = [serialize_account_policy(item) for item in PersistenceService(db).list_account_policies()]
    return AccountPolicyListResponse(items=items, total=len(items))


@app.put("/api/orchestration/playbooks/{playbook_key}", response_model=CampaignPlaybookRead)
def upsert_campaign_playbook(
    playbook_key: str,
    payload: CampaignPlaybookUpsertRequest,
    _: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> CampaignPlaybookRead:
    return serialize_campaign_playbook(PersistenceService(db).upsert_campaign_playbook(playbook_key, payload))


@app.get("/api/orchestration/playbooks", response_model=CampaignPlaybookListResponse)
def list_campaign_playbooks(
    _: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> CampaignPlaybookListResponse:
    items = [serialize_campaign_playbook(item) for item in PersistenceService(db).list_campaign_playbooks()]
    return CampaignPlaybookListResponse(items=items, total=len(items))


@app.post("/api/orchestration/material-decisions", response_model=MaterialDecisionRead)
def create_material_decision(
    payload: MaterialDecisionCreateRequest,
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> MaterialDecisionRead:
    return serialize_material_decision(
        PersistenceService(db).create_material_decision(payload, operator_id=operator_id)
    )


@app.get("/api/orchestration/material-decisions", response_model=MaterialDecisionListResponse)
def list_material_decisions(
    advertiser_id: int | None = Query(default=None, gt=0),
    statuses: list[str] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    _: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> MaterialDecisionListResponse:
    items, total = PersistenceService(db).list_material_decisions(
        advertiser_id=advertiser_id,
        statuses=statuses,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return MaterialDecisionListResponse(
        items=[serialize_material_decision(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@app.post("/api/orchestration/strategy-reviews", response_model=StrategyReviewRead)
def create_strategy_review(
    payload: StrategyReviewCreateRequest,
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> StrategyReviewRead:
    return serialize_strategy_review(
        PersistenceService(db).create_strategy_review(payload, operator_id=operator_id)
    )


@app.get("/api/orchestration/strategy-reviews", response_model=StrategyReviewListResponse)
def list_strategy_reviews(
    advertiser_id: int | None = Query(default=None, gt=0),
    playbook_key: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    _: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> StrategyReviewListResponse:
    items, total = PersistenceService(db).list_strategy_reviews(
        advertiser_id=advertiser_id,
        playbook_key=playbook_key,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return StrategyReviewListResponse(
        items=[serialize_strategy_review(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@app.post("/api/orchestration/delivery-plan", response_model=DeliveryPlanResponse)
def build_delivery_plan(
    payload: DeliveryPlanRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> DeliveryPlanResponse:
    return PersistenceService(db).build_delivery_plan(
        payload,
        strategy_service=build_strategy_service(settings),
        operator_id=operator_id,
        require_policy=settings.qianchuan_require_account_policy_for_writes,
    )


@app.post("/api/actions/budget-updates")
def update_budget(
    payload: BudgetUpdateRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_action("ad_budget.update", payload, operator_id, db, settings, "apply_budget_updates")


@app.post("/api/actions/bid-updates")
def update_bid(
    payload: BidUpdateRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_action("ad_bid.update", payload, operator_id, db, settings, "apply_bid_updates")


@app.post("/api/actions/roi-goal-updates")
def update_roi_goal(
    payload: RoiGoalUpdateRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_action("roi_goal.update", payload, operator_id, db, settings, "apply_roi_goal_updates")


@app.post("/api/actions/ad-status")
def update_ad_status(
    payload: AdStatusUpdateRequest,
    settings: Settings = Depends(get_runtime_settings),
    operator_id: str = Depends(get_operator_id),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return _write_action("ad_status.update", payload, operator_id, db, settings, "update_ad_status")


def _call_read(func, settings: Settings) -> dict[str, Any]:
    try:
        return func(build_strategy_service(settings))
    except (QianchuanApiError, StrategyError) as exc:
        raise _to_http_exception(exc) from exc


def _write_action(
    action_code: str,
    payload,
    operator_id: str,
    db: Session,
    settings: Settings,
    method_name: str,
) -> dict[str, Any]:
    service = build_strategy_service(settings)
    try:
        PersistenceService(db).ensure_account_policy_allows(
            action_code,
            payload,
            require_policy=settings.qianchuan_require_account_policy_for_writes,
        )
        PersistenceService(db).ensure_action_cooldown(
            action_code,
            payload,
            settings.qianchuan_write_cooldown_minutes,
        )
        if action_code == "ad_budget.update" and settings.qianchuan_autonomous_enabled:
            persistence = PersistenceService(db)
            profile = persistence.get_autonomy_profile(payload.advertiser_id)
            if profile is None:
                raise ValidationError(
                    "Autonomy profile is missing. Ask the user for the required financial "
                    "inputs before any budget update."
                )
            persistence.ensure_autonomous_budget_limits(
                payload,
                advertiser_id=str(settings.qianchuan_autonomous_advertiser_id),
                min_budget=settings.qianchuan_autonomous_min_budget,
                max_budget=profile.max_budget,
                max_increase_rate=settings.qianchuan_autonomous_increase_rate,
                max_decrease_rate=settings.qianchuan_autonomous_decrease_rate,
                max_actions_per_day=settings.qianchuan_autonomous_max_actions_per_day,
                max_daily_increase=profile.max_daily_increase,
            )
        response = getattr(service, method_name)(payload)
    except (QianchuanApiError, StrategyError) as exc:
        PersistenceService(db).write_audit(
            operator_id=operator_id,
            action_code=action_code,
            target_type="qianchuan_ad",
            target_id=str(payload.advertiser_id),
            request_payload=payload.model_dump(mode="json"),
            response_payload={},
            error_message=str(exc),
        )
        raise _to_http_exception(exc) from exc
    PersistenceService(db).write_audit(
        operator_id=operator_id,
        action_code=action_code,
        target_type="qianchuan_ad",
        target_id=str(payload.advertiser_id),
        request_payload=payload.model_dump(mode="json"),
        response_payload=response,
    )
    return response


def _write_tool_action(
    action_code: str,
    payload: ToolCallRequest,
    operator_id: str,
    db: Session,
    settings: Settings,
    method_name: str,
) -> dict[str, Any]:
    request_payload = payload.payload
    target_id = _target_id_from_payload(request_payload)
    service = build_strategy_service(settings)
    persistence = PersistenceService(db)
    try:
        if not request_payload:
            raise ValidationError("Write tool payload must include advertiser_id.")
        persistence.ensure_launch_authorization_allows(action_code, request_payload)
        persistence.ensure_account_policy_allows(
            action_code,
            request_payload,
            require_policy=settings.qianchuan_require_account_policy_for_writes,
        )
        persistence.ensure_action_cooldown(
            action_code,
            request_payload,
            settings.qianchuan_write_cooldown_minutes,
        )
        if (
            action_code
            in {
                "tool.qianchuan_campaign_create_v1",
                "tool.qianchuan_ad_create_v1",
                "tool.qianchuan_uni_aweme_ad_create_v1",
            }
            and settings.qianchuan_write_enabled
            and payload.confirm
        ):
            advertiser_id = int(request_payload["advertiser_id"])
            product_ids = payload_product_ids(request_payload)
            inventory: dict[str, int] | None = None
            product_channels: dict[str, dict[str, Any]] | None = None
            product_card_image_ids: dict[str, set[str]] | None = None
            uni_aweme_account: dict[str, Any] | None = None
            if product_ids:
                product_params = {
                    "advertiser_id": advertiser_id,
                    "page": 1,
                    "page_size": min(100, len(product_ids)),
                }
                if action_code == "tool.qianchuan_uni_aweme_ad_create_v1":
                    aweme_id = extract_required_aweme_id(request_payload)
                    product_params["aweme_id"] = aweme_id
                    product_params["filtering"] = {
                        "product_ids": [int(item) for item in sorted(product_ids)]
                    }
                    products = service.get_uni_promotion_products(product_params)
                    eligibility_result = service.get_uni_authorized_aweme_accounts(
                        {
                            "advertiser_id": advertiser_id,
                            "filtering": {
                                "marketing_goal": str(
                                    request_payload.get("marketing_goal") or ""
                                ),
                                "scene": "CREATE",
                            },
                            "page": 1,
                            "page_size": 100,
                        }
                    )
                    uni_aweme_account = extract_uni_aweme_account(
                        eligibility_result, aweme_id
                    )
                    enforce_uni_aweme_create_eligibility(
                        uni_aweme_account,
                        aweme_id=aweme_id,
                        marketing_goal=str(
                            request_payload.get("marketing_goal") or ""
                        ),
                    )
                else:
                    product_params["filter"] = {
                        "product_ids": [int(item) for item in sorted(product_ids)]
                    }
                    products = service.get_available_products(product_params)
                inventory = extract_product_inventory(products)
                product_channels = extract_product_channels(products)
                product_card_image_ids = extract_product_card_image_ids(products)
            balance = extract_account_valid_balance(
                service.get_account_balance({"advertiser_id": advertiser_id})
            )
            persistence.ensure_launch_authorization_allows(
                action_code,
                request_payload,
                product_inventory=inventory,
                product_channels=product_channels,
                product_card_image_ids=product_card_image_ids,
                account_valid_balance=balance,
                preflight_complete=True,
            )
        response = getattr(service, method_name)(payload)
    except (QianchuanApiError, StrategyError) as exc:
        error_response = (
            {
                "code": exc.code,
                "message": str(exc),
                "request_id": exc.request_id,
                "http_status": exc.status_code,
                "official_response": exc.response_payload,
            }
            if isinstance(exc, QianchuanApiError)
            else {}
        )
        persistence.write_audit(
            operator_id=operator_id,
            action_code=action_code,
            target_type="qianchuan_launch",
            target_id=target_id,
            request_payload=payload.model_dump(mode="json"),
            response_payload=error_response,
            error_message=str(exc),
        )
        raise _to_http_exception(exc) from exc
    persistence.write_audit(
        operator_id=operator_id,
        action_code=action_code,
        target_type="qianchuan_launch",
        target_id=target_id,
        request_payload=payload.model_dump(mode="json"),
        response_payload=response,
    )
    return response


def _target_id_from_payload(payload) -> str:
    advertiser_id = payload_advertiser_id(payload)
    return str(advertiser_id) if advertiser_id is not None else "unknown"


def _to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, QianchuanApiError):
        message = str(exc)
        if exc.code is not None:
            message = f"{message} code={exc.code}"
        if exc.status_code is not None:
            message = f"{message} http_status={exc.status_code}"
        if exc.request_id:
            message = f"{message} request_id={exc.request_id}"
        return HTTPException(status_code=502, detail=message)
    if isinstance(exc, WriteDisabledError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ConfirmationRequiredError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))
