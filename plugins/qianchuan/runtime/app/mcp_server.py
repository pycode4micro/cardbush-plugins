from __future__ import annotations

import json
import httpx
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.session import ServerSession
from mcp.types import ToolAnnotations
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import __version__
from app.autonomous import AutonomousBudgetController, autonomy_profile_questions
from app.config import Settings, get_settings
from app.db import build_engine, create_session_factory, init_db
from app.gateway import OAuthGatewayClient, TokenResolutionError, resolve_access_token
from app.oauth_session import save_selection, selected_token
from app.launch import (
    UNI_AWEME_LAUNCH_TYPE,
    extract_account_valid_balance,
    extract_product_channels,
    extract_product_card_image_ids,
    extract_product_inventory,
    extract_required_aweme_id,
    extract_uni_aweme_account,
    enforce_uni_aweme_create_eligibility,
    launch_authorization_questions,
    launch_preview,
    normalize_launch_type,
)
from app.orchestration import payload_advertiser_id, payload_product_ids
from app.material_binding import (
    ProductCardBindingValidationError,
    build_uni_promotion_product_card_binding_payload,
)
from app.carousel_binding import (
    CarouselBindingValidationError,
    build_uni_promotion_carousel_binding_payload,
)
from app.carousel_library import (
    CarouselLibraryValidationError,
    build_carousel_create_payload,
)
from app.persistence import (
    DecisionNotFoundError,
    PersistenceService,
    serialize_account_policy,
    serialize_autonomy_profile,
    serialize_campaign_playbook,
    serialize_launch_authorization,
    serialize_decision,
    serialize_material_decision,
    serialize_strategy_review,
)
from app.qianchuan_client import QianchuanApiError, QianchuanClient, QianchuanConfig
from app.schemas import (
    DEFAULT_ACCOUNT_REPORT_FIELDS,
    DEFAULT_MATERIAL_REPORT_FIELDS,
    DEFAULT_ROI_REPORT_FIELDS,
    AccountPolicyUpsertRequest,
    AdStatusUpdateRequest,
    BidUpdateRequest,
    BudgetUpdateRequest,
    AutonomyProfileUpsertRequest,
    CampaignPlaybookUpsertRequest,
    LaunchAuthorizationUpsertRequest,
    DeliveryPlanRequest,
    MaterialDecisionCreateRequest,
    MaterialFatigueRequest,
    ReportQuery,
    RoiDiagnosisRequest,
    RoiGoalUpdateRequest,
    RoiStrategyRequest,
    SnapshotSyncRequest,
    StrategyReviewCreateRequest,
    ToolCallRequest,
)
from app.tools import get_tool, list_tools
from app.strategy import (
    ConfirmationRequiredError,
    QianchuanStrategyService,
    StrategyError,
    ValidationError,
    WriteDisabledError,
)
from app.video_upload import VideoUploadValidationError, prepare_video_upload
from app.image_upload import ImageUploadValidationError, prepare_image_upload


@dataclass(frozen=True)
class McpAppContext:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]


McpContext = Context[ServerSession, McpAppContext]


@asynccontextmanager
async def app_lifespan(_server: FastMCP) -> AsyncIterator[McpAppContext]:
    settings = get_settings()
    settings.validate_for_startup()
    engine = build_engine(settings)
    init_db(engine)
    try:
        yield McpAppContext(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
        )
    finally:
        engine.dispose()


mcp = FastMCP(
    name="Qianchuan ROI Tool Service",
    stateless_http=True,
    json_response=True,
    lifespan=app_lifespan,
)


@mcp.resource("qianchuan://tool-specs", mime_type="application/json")
def qianchuan_tool_specs_resource() -> str:
    """Return supported Qianchuan API tool specs."""
    items = list_tools()
    return json.dumps({"items": items, "total": len(items)}, ensure_ascii=True)


@mcp.prompt(title="Qianchuan ROI Operator")
def qianchuan_roi_operator_prompt() -> str:
    """Prompt that guides an agent through guarded Qianchuan ROI optimization."""
    return (
        "You operate Qianchuan ads through guarded tools. Start with account discovery, "
        "then call qianchuan_discover_launch_assets with the intended launch_type. Prefer "
        "UNI_AWEME for PC full-domain product delivery: it creates the promotion plan directly "
        "and must not be preceded by a standard Campaign or replaced by an Aweme promotion order. "
        "Read the locked autonomy profile and "
        "launch authorization; if either is missing, ask every returned question and save the "
        "user's explicit answers once. Preview exact launch payloads before creation. Reuse "
        "locked values until the user asks to change them. After a full-domain create, reconcile "
        "data.ad_id through the uni-promotion list and detail tools. Use the dedicated uni-promotion "
        "budget, ROI2, and reversible status tools for optimization. Write tools require confirm=true, "
        "a concrete reason, account-policy permission, and QIANCHUAN_WRITE_ENABLED=true. Never "
        "delete objects autonomously and never widen product, identity, material, or budget scope. "
        "Video upload is a separate material_upload action: upload only from the configured local "
        "root, verify the returned video_id, and never bind it automatically."
    )


@mcp.tool()
def qianchuan_health(ctx: McpContext) -> dict[str, Any]:
    """Check local service configuration visible to MCP."""
    settings = _settings(ctx)
    return {
        "status": "ok",
        "version": __version__,
        "has_direct_access_token": bool(settings.qianchuan_access_token),
        "has_oauth_gateway": bool(settings.oauth_gateway_base_url and settings.oauth_gateway_hmac_secret),
        "write_enabled": settings.qianchuan_write_enabled,
        "outbound_http_trust_env": settings.outbound_http_trust_env,
        "api_base_url": settings.qianchuan_api_base_url,
        "database_url": settings.database_url,
        "write_cooldown_minutes": settings.qianchuan_write_cooldown_minutes,
        "roi_write_cooldown_minutes": settings.qianchuan_roi_write_cooldown_minutes,
        "status_write_cooldown_minutes": settings.qianchuan_status_write_cooldown_minutes,
        "video_upload_root": settings.qianchuan_video_upload_root,
        "video_upload_max_mb": settings.qianchuan_video_upload_max_mb,
        "image_upload_root": settings.qianchuan_image_upload_root,
        "image_upload_max_mb": settings.qianchuan_image_upload_max_mb,
    }


@mcp.tool()
def qianchuan_get_optimization_capabilities(
    ctx: McpContext,
    advertiser_id: int,
) -> dict[str, Any]:
    """Show official interface coverage and which guarded optimization writes policy permits."""
    guarded_optimization_tools = {
        "account_budget_update": ["qianchuan_update_account_budget"],
        "budget_update": [
            "qianchuan_update_budget",
            "qianchuan_update_uni_promotion_ad_budget",
            "qianchuan_update_uni_promotion_control_task_budget",
        ],
        "bid_update": ["qianchuan_update_bid"],
        "roi_goal_update": [
            "qianchuan_update_roi_goal",
            "qianchuan_update_uni_promotion_ad_roi2_goal",
        ],
        "ad_status_update": [
            "qianchuan_update_ad_status",
            "qianchuan_update_uni_promotion_ad_status",
        ],
        "ad_update": [
            "qianchuan_update_ad",
            "qianchuan_update_uni_aweme_ad",
            "qianchuan_upgrade_uni_promotion_to_multiplier",
            "qianchuan_update_ad_region",
            "qianchuan_update_ad_schedule_date",
            "qianchuan_update_ad_schedule_fixed_range",
            "qianchuan_update_uni_promotion_ad_name",
            "qianchuan_update_uni_promotion_ad_schedule",
        ],
        "campaign_update": [
            "qianchuan_update_campaign",
            "qianchuan_update_campaign_status_batch",
        ],
        "material_upload": ["qianchuan_upload_video", "qianchuan_upload_image"],
        "carousel_create": ["qianchuan_create_carousel_material"],
        "material_bind": [
            "qianchuan_add_uni_promotion_product_card_images",
            "qianchuan_add_uni_promotion_ad_material",
        ],
        "control_task_create": [
            "qianchuan_create_uni_promotion_control_task",
            "qianchuan_create_uni_promotion_smart_control_task",
        ],
        "control_task_update": [
            "qianchuan_update_uni_promotion_control_task",
            "qianchuan_update_uni_promotion_control_task_status",
            "qianchuan_update_uni_promotion_control_task_budget",
            "qianchuan_update_uni_promotion_control_task_duration",
            "qianchuan_update_uni_promotion_smart_control_task_status",
        ],
        "order_budget_update": [
            "qianchuan_add_aweme_order_budget",
            "qianchuan_add_aweme_uni_promotion_order_budget",
        ],
        "smart_boost_update": ["qianchuan_set_smart_boost"],
    }
    tool_specs = list_tools()
    with _db_session(ctx) as db:
        policy = PersistenceService(db).get_account_policy(advertiser_id)
        allowed_actions = set(policy.allowed_actions if policy else [])
        policy_payload = serialize_account_policy(policy) if policy else None
    return {
        "version": __version__,
        "advertiser_id": advertiser_id,
        "official_catalog": {
            "total": len(tool_specs),
            "read": sum(1 for item in tool_specs if not item["write"]),
            "write": sum(1 for item in tool_specs if item["write"]),
            "read_access": "All catalogued official GET interfaces are callable read-only.",
            "write_access": "Optimization writes use dedicated guarded MCP tools; raw writes remain blocked unless separately authorized.",
        },
        "global_write_enabled": _settings(ctx).qianchuan_write_enabled,
        "account_policy": _model_dump(policy_payload) if policy_payload else None,
        "optimization_actions": [
            {
                "policy_action": action,
                "enabled_for_account": bool(
                    policy and policy.write_enabled and action in allowed_actions
                ),
                "tools": tools,
            }
            for action, tools in guarded_optimization_tools.items()
        ],
        "safety": {
            "delete_is_not_an_optimization_action": True,
            "standard_and_uni_status_tools_allow": ["ENABLE", "DISABLE"],
            "policy_is_not_modified_by_this_check": True,
        },
    }


@mcp.tool()
def qianchuan_list_tool_specs() -> dict[str, Any]:
    """List supported raw Qianchuan API tool specs."""
    items = list_tools()
    return {"items": items, "total": len(items)}


@mcp.tool()
def qianchuan_search_tool_specs(
    query: str = "",
    method: str | None = None,
    write: bool | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Search the complete official Qianchuan interface catalog without returning all entries."""
    normalized_query = query.strip().lower()
    normalized_method = method.strip().upper() if method else None
    if normalized_method not in {None, "GET", "POST"}:
        raise ToolError("method must be GET or POST.")
    if limit < 1 or limit > 200:
        raise ToolError("limit must be between 1 and 200.")
    matches = []
    for item in list_tools():
        haystack = " ".join(
            [str(item.get("key") or ""), str(item.get("title") or ""), str(item.get("path") or "")]
        ).lower()
        if normalized_query and normalized_query not in haystack:
            continue
        if normalized_method and item.get("method") != normalized_method:
            continue
        if write is not None and bool(item.get("write")) is not write:
            continue
        matches.append(item)
    return {"items": matches[:limit], "matched": len(matches), "returned": min(len(matches), limit)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
def qianchuan_start_timed_delivery(
    ctx: McpContext, advertiser_id: int, ad_ids: list[int], ends_at: str,
    confirm: bool = False, reason: str | None = None,
) -> dict[str, Any]:
    """Enable existing PC full-domain plans until an ISO-8601 deadline with timezone.

    One call verifies exact plans/permissions, durably registers deadline pauses, enables
    each plan at most once and reads back results. Does not create plans or change budgets.
    Requires a running local worker; failure returns actionable status with no enable.
    Repeated calls do not re-enable plans. Agent need not register a separate deadline or
    maintain deduplication state. attention means unknown/failed, never guaranteed running.
    """
    if not confirm or len((reason or "").strip()) < 8:
        return {"status": "confirmation_required", "message": "请确认启用指定计划并授权到期暂停；reason 至少8字符。", "writes_submitted": False}
    if not ad_ids or len(ad_ids) > 100 or len(set(ad_ids)) != len(ad_ids):
        return {"status": "invalid_input", "message": "ad_ids 应为1至100个不重复计划ID。", "writes_submitted": False}
    try:
        qianchuan_register_delivery_deadline(ctx, advertiser_id, ad_ids, ends_at, True)
    except Exception:
        return {"status": "blocked", "message": "无法登记到期保障。请检查后台状态、带时区的截止时间、计划身份和现有权限。未启用计划。", "writes_submitted": False}
    from app.deadline import snapshot
    factory = ctx.request_context.lifespan_context.session_factory
    records = {r["ad_id"]: r for r in snapshot(factory)}
    import time
    if any(records.get(str(ad), {}).get("state") != "scheduled" or records[str(ad)]["end_epoch"] <= time.time() for ad in ad_ids):
        return {"status": "blocked", "message": "任务已到期或进入暂停流程，不重新启用。", "writes_submitted": False}
    from app.timed_delivery import enable_once
    def read(ad):
        data = _strategy_service(ctx).get_uni_promotion_ad_detail({"advertiser_id": advertiser_id, "ad_id": int(ad)}).get("data", {})
        if str(data.get("ad_id")) != ad:
            raise ValueError("Plan identity mismatch")
        return data.get("opt_status")
    def enable(ad):
        return qianchuan_update_uni_promotion_ad_status(ctx,
            payload={"advertiser_id": advertiser_id, "ad_ids": [int(ad)], "opt_status": "ENABLE"},
            confirm=True, reason=reason)
    items = enable_once(factory, ad_ids, read, enable)
    return {"status": "running" if all(r["state"] == "running" for r in items) else "attention",
            "items": items, "ends_at": ends_at, "deadline_pause_registered": True,
            "message": "已逐计划回读；attention 不表示启用成功。后台负责到期暂停，不负责ROI策略调整。"}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def qianchuan_register_delivery_deadline(
    ctx: McpContext, advertiser_id: int, ad_ids: list[int], ends_at: str, confirm: bool = False,
) -> dict[str, Any]:
    """Authorize only an at-most-once deadline DISABLE for exact existing UNI plans.

    Requires the independent REST service or deadline worker to stay running. Does not
    enable ads or guarantee a spend cap. ends_at must include timezone. No silent extension.
    """
    from app.deadline import register, snapshot, worker_status
    factory = ctx.request_context.lifespan_context.session_factory
    if not worker_status(factory)["online"]:
        raise ToolError("Deadline worker is offline; start REST or python -m app.deadline_worker first")
    if not confirm:
        raise ToolError("Explicit deadline pause authorization is required")
    if not _settings(ctx).qianchuan_write_enabled:
        raise ToolError("Global writes disabled; cannot arm a live pause task")
    with _db_session(ctx) as db:
        PersistenceService(db).ensure_account_policy_allows(
            "tool.qianchuan_uni_promotion_ad_status_update_v1",
            {"advertiser_id": advertiser_id, "ad_ids": ad_ids, "opt_status": "DISABLE"},
            require_policy=True)
    service = _strategy_service(ctx)
    for ad_id in ad_ids:
        data = service.get_uni_promotion_ad_detail({"advertiser_id": advertiser_id, "ad_id": ad_id}).get("data", {})
        if str(data.get("ad_id")) != str(ad_id) or data.get("opt_status") not in {"ENABLE", "DISABLE"}:
            raise ToolError("Could not verify exact plan identity and reversible state")
    factory = ctx.request_context.lifespan_context.session_factory
    try:
        register(factory, advertiser_id, ad_ids, ends_at)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {"status": "registered", "worker_required": "Keep REST or python -m app.deadline_worker running independently", "items": [r for r in snapshot(factory) if r["ad_id"] in set(map(str, ad_ids))]}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
def qianchuan_delivery_deadline_status(ctx: McpContext) -> dict[str, Any]:
    """Read durable deadline states. attention is not safely paused; notify the user."""
    from app.deadline import snapshot, worker_status
    factory = ctx.request_context.lifespan_context.session_factory
    return {"worker": worker_status(factory), "items": snapshot(factory)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
def qianchuan_request_delivery_stop(ctx: McpContext, ad_ids: list[int], confirm: bool = False) -> dict[str, Any]:
    """Bring already registered deadlines forward. Does not claim official pause success."""
    from app.deadline import stop_now
    if not confirm:
        raise ToolError("Explicit stop confirmation required")
    stop_now(ctx.request_context.lifespan_context.session_factory, ad_ids)
    return {"status": "stop_requested", "verified_paused": False, "next_tool": "qianchuan_delivery_deadline_status"}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def qianchuan_report_routing_guide() -> dict[str, Any]:
    """报表选路说明：账户消耗、商品全域、直播全域、历史标准、审核建议各用什么。

    不发网络请求。先读本工具，禁止把19个主题轮询后相加或用空标准报表断言零消耗。
    """
    from app.spend_report import guide
    return guide()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
def qianchuan_start_spend_report(ctx: McpContext, start_date: str, end_date: str,
                               advertiser_ids: list[str] | None = None, marketing_goal: str = "ALL") -> dict[str, Any]:
    """查询指定日期PC千川消耗的首选入口；启动只读后台查询，立即返回job_id。

    日期YYYY-MM-DD，北京时间；最多31天。省略advertiser_ids查询当前网关token所有
    active QIANCHUAN账户与本地白名单的交集。显式账户也必须在此范围，不能传店铺ID。
    固定all_promotion/get、stat_cost_for_roi2(元)，全域+乘方各一次；不扫描19个主题。
    marketing_goal=ALL是直播+商品合计；VIDEO_PROM_GOODS不等于纯商品卡流量。
    不含随心推、历史标准投放。用qianchuan_get_spend_report_result轮询，只有complete=true
    才能报完整总额；partial/running中的known_cost_yuan只是已知小计。任务仅进程内保留。
    不写广告、不启动投放；认证可能刷新token。不要重复启动同一运行中任务。
    """
    from app.spend_report import start_report, validate_dates
    validate_dates(start_date, end_date)
    settings = _settings(ctx)
    if not (settings.oauth_gateway_base_url and settings.oauth_gateway_hmac_secret):
        return {"status": "blocked", "reason": "resolved_gateway_accounts_required"}
    payload = OAuthGatewayClient(settings).get_access_token_response()
    rows = payload.get("advertisers")
    if not payload.get("access_token") or not isinstance(rows, list):
        raise ToolError("Gateway missing access token or resolved advertisers")
    allowed = set(settings.qianchuan_allowed_advertiser_ids)
    accounts = {str(a["advertiser_id"]): {"advertiser_id": str(a["advertiser_id"]),
                "advertiser_name": a.get("advertiser_name")} for a in rows
                if a.get("status") == "active" and a.get("account_role") == "QIANCHUAN"
                and (str(a.get("advertiser_id")) in allowed or
                     (not allowed and settings.qianchuan_allow_all_authorized_advertisers))}
    if advertiser_ids is not None:
        if not advertiser_ids or not set(advertiser_ids) <= accounts.keys():
            raise ToolError("Requested accounts are empty or outside resolved authorization/local allowlist")
        accounts = {a: accounts[a] for a in dict.fromkeys(advertiser_ids)}
    client = QianchuanClient(QianchuanConfig(api_base_url=settings.qianchuan_api_base_url,
        timeout_seconds=settings.qianchuan_request_timeout_seconds,
        allowed_advertiser_ids=frozenset(accounts), trust_env=settings.outbound_http_trust_env))
    return start_report(client, payload["access_token"], list(accounts.values()), start_date, end_date, marketing_goal)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def qianchuan_get_spend_report_result(job_id: str) -> dict[str, Any]:
    """获取消耗查询进度/结果，无网络请求。不把known_cost_yuan当完整总额。

    complete=true才使用total_cost_yuan；逐账户parts保留场景、错误码、request_id。
    failed/missing指标不是0；任务过期或服务重启返回not_found，可重新发起只读查询。
    """
    from app.spend_report import result
    return result(job_id)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def qianchuan_connection_status(ctx: McpContext) -> dict[str, Any]:
    """Check live OAuth connection without exposing credentials or changing account policies."""
    settings = _settings(ctx)
    if settings.qianchuan_access_token:
        return {"status": "direct_token_configured", "next_tool": "qianchuan_get_accounts", "verified": False}
    if not (settings.oauth_gateway_base_url and settings.oauth_gateway_hmac_secret):
        return {"status": "setup_required", "required": ["OAUTH_GATEWAY_BASE_URL", "OAUTH_GATEWAY_HMAC_SECRET"], "security_configuration_is_manual": True}
    try:
        token = selected_token(settings)
        if not token:
            return {"status": "login_required", "next_tool": "qianchuan_oauth_start_url"}
        data = OAuthGatewayClient(settings).get_resolved_advertisers()
        return {"status": "connected", "token_no": token, "accounts": data["data"]["list"], "policies_unchanged": True}
    except (TokenResolutionError, ValueError, OSError, httpx.HTTPError) as exc:
        message = str(exc)
        status = "reauthorization_required" if "reauthorization_required" in message else "connection_error"
        return {"status": status, "next_tool": "qianchuan_oauth_start_url" if status == "reauthorization_required" else "qianchuan_oauth_authorizations"}


@mcp.tool()
def qianchuan_oauth_select_authorization(
    ctx: McpContext, token_no: str, required_advertiser_ids: list[str], confirm: bool = False,
) -> dict[str, Any]:
    """After user login, verify and persist an explicit authorization selection. No policy changes.

    Required advertiser IDs must cover the user's intended accounts. Does not enable writes.
    Selection is shared by local REST and MCP clients without restarting them.
    """
    if not confirm or not required_advertiser_ids:
        raise ToolError("Explicit confirmation and intended advertiser IDs are required.")
    settings = _settings(ctx)
    if settings.qianchuan_access_token:
        raise ToolError("Direct token takes precedence; change that security configuration manually first.")
    try:
        client = OAuthGatewayClient(settings, token_no=token_no)
        records = client.list_authorizations().get("items", [])
        if not any(r.get("token_no") == token_no and r.get("status") == "active" for r in records):
            raise ToolError("Selected authorization is not active; authorize again.")
        data = client.get_resolved_advertisers()
        ids = {str(a.get("advertiser_id")) for a in data["data"]["list"]}
        if not set(required_advertiser_ids).issubset(ids):
            raise ToolError("Authorization does not cover all intended accounts; selection unchanged.")
        save_selection(settings, token_no)
        return {"status": "connected", "token_no": token_no, "advertiser_ids": sorted(ids), "policies_unchanged": True, "restart_required": False}
    except (TokenResolutionError, ValueError, OSError, httpx.HTTPError) as exc:
        raise ToolError("Authorization verification failed; selection unchanged. Check connection or reauthorize.") from exc


@mcp.tool()
def qianchuan_oauth_start_url(ctx: McpContext, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create an OAuth authorization URL through the public OAuth gateway."""
    try:
        return OAuthGatewayClient(_settings(ctx)).create_start_url(payload or {})
    except TokenResolutionError as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_oauth_authorizations(ctx: McpContext) -> dict[str, Any]:
    """List OAuth authorizations stored in the public OAuth gateway."""
    try:
        return OAuthGatewayClient(_settings(ctx)).list_authorizations()
    except TokenResolutionError as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_get_accounts(ctx: McpContext) -> dict[str, Any]:
    """List resolved Qianchuan advertiser accounts for the selected OAuth token."""
    settings = _settings(ctx)
    if not settings.qianchuan_access_token:
        try:
            return OAuthGatewayClient(settings).get_resolved_advertisers()
        except TokenResolutionError as exc:
            raise _tool_error(exc) from exc
    return _read_call(ctx, lambda service: service.get_authorized_accounts())


@mcp.tool()
def qianchuan_get_available_products(
    ctx: McpContext,
    advertiser_id: int,
    aweme_id: int | None = None,
    campaign_scene: str | None = None,
    filtering: dict[str, Any] | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """List officially available merchant products, including price, sales, and inventory."""
    params: dict[str, Any] = {
        "advertiser_id": advertiser_id,
        "filter": filtering or {},
        "page": page,
        "page_size": page_size,
    }
    if aweme_id is not None:
        params["aweme_id"] = aweme_id
    if campaign_scene:
        params["campaign_scene"] = campaign_scene
    return _read_call(ctx, lambda service: service.get_available_products(params))


@mcp.tool()
def qianchuan_get_aweme_available_products(
    ctx: McpContext, params: dict[str, Any]
) -> dict[str, Any]:
    """List products available to an authorized Aweme account."""
    return _read_call(ctx, lambda service: service.get_aweme_available_products(params))


@mcp.tool()
def qianchuan_get_authorized_aweme_accounts(
    ctx: McpContext,
    advertiser_id: int,
    filtering: dict[str, Any] | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """List authorized Aweme accounts and their video/live commerce permissions."""
    params = {
        "advertiser_id": advertiser_id,
        "filtering": filtering or {},
        "page": page,
        "page_size": page_size,
    }
    return _read_call(ctx, lambda service: service.get_authorized_aweme_accounts(params))


@mcp.tool()
def qianchuan_get_uni_authorized_aweme_accounts(
    ctx: McpContext, params: dict[str, Any]
) -> dict[str, Any]:
    """List authorized Aweme accounts eligible for all-domain promotion."""
    return _read_call(ctx, lambda service: service.get_uni_authorized_aweme_accounts(params))


@mcp.tool()
def qianchuan_get_uni_authorizable_shops(
    ctx: McpContext,
    advertiser_id: int,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """List official shops that may be used in a product full-domain authorization request."""
    params = {"advertiser_id": advertiser_id, "page": page, "page_size": page_size}
    return _read_call(ctx, lambda service: service.get_uni_authorizable_shops(params))


@mcp.tool()
def qianchuan_get_account_balance(ctx: McpContext, advertiser_id: int) -> dict[str, Any]:
    """Get the official Qianchuan account balance used by launch preflight."""
    return _read_call(
        ctx, lambda service: service.get_account_balance({"advertiser_id": advertiser_id})
    )


@mcp.tool()
def qianchuan_get_ad_quota(ctx: McpContext, advertiser_id: int) -> dict[str, Any]:
    """Get active-ad quota before creating more delivery plans."""
    return _read_call(ctx, lambda service: service.get_ad_quota({"advertiser_id": advertiser_id}))


@mcp.tool()
def qianchuan_get_ad_reject_reasons(
    ctx: McpContext, params: dict[str, Any]
) -> dict[str, Any]:
    """Get official audit rejection reasons for created ads."""
    return _read_call(ctx, lambda service: service.get_ad_reject_reasons(params))


@mcp.tool()
def qianchuan_get_ad_learning_status(
    ctx: McpContext, params: dict[str, Any]
) -> dict[str, Any]:
    """Get learning status for post-launch monitoring."""
    return _read_call(ctx, lambda service: service.get_ad_learning_status(params))


@mcp.tool()
def qianchuan_discover_launch_assets(
    ctx: McpContext,
    advertiser_id: int = 1811432276115675,
    launch_type: str = "STANDARD",
    aweme_id: int | None = None,
    campaign_scene: str | None = None,
    product_filtering: dict[str, Any] | None = None,
    aweme_filtering: dict[str, Any] | None = None,
    page_size: int = 100,
) -> dict[str, Any]:
    """Discover STANDARD or UNI_AWEME products, Aweme permissions, balance, and quota read-only."""
    try:
        _ensure_autonomous_advertiser(ctx, advertiser_id)
        size = max(1, min(page_size, 100))
        service = _strategy_service(ctx)
        normalized_launch_type = normalize_launch_type(launch_type)
        product_params: dict[str, Any] = {
            "advertiser_id": advertiser_id,
            "page": 1,
            "page_size": size,
        }
        if normalized_launch_type == UNI_AWEME_LAUNCH_TYPE:
            selected_aweme_id = aweme_id
            with _db_session(ctx) as db:
                authorization = PersistenceService(db).get_launch_authorization(advertiser_id)
                allowed_aweme_ids = list(authorization.allowed_aweme_ids or []) if authorization else []
            if selected_aweme_id is None and len(allowed_aweme_ids) == 1:
                selected_aweme_id = int(allowed_aweme_ids[0])
            if selected_aweme_id is None:
                raise ValidationError(
                    "UNI_AWEME discovery requires aweme_id when the locked launch "
                    "authorization does not contain exactly one allowed Aweme account."
                )
            if allowed_aweme_ids and str(selected_aweme_id) not in set(allowed_aweme_ids):
                raise ValidationError("aweme_id is outside the locked launch authorization.")
            product_params["filtering"] = product_filtering or {}
            product_params["aweme_id"] = selected_aweme_id
            products = service.get_uni_promotion_products(product_params)
            eligibility_filter = {
                "marketing_goal": "VIDEO_PROM_GOODS",
                "scene": "CREATE",
            }
            eligibility_filter.update(aweme_filtering or {})
            aweme_accounts = service.get_uni_authorized_aweme_accounts(
                {
                    "advertiser_id": advertiser_id,
                    "filtering": eligibility_filter,
                    "page": 1,
                    "page_size": size,
                }
            )
        else:
            product_params["filter"] = product_filtering or {}
            if campaign_scene:
                product_params["campaign_scene"] = campaign_scene
            products = service.get_available_products(product_params)
            aweme_accounts = service.get_authorized_aweme_accounts(
                {
                    "advertiser_id": advertiser_id,
                    "filtering": aweme_filtering or {},
                    "page": 1,
                    "page_size": size,
                }
            )
        return {
            "status": "ok",
            "read_only": True,
            "advertiser_id": advertiser_id,
            "launch_type": normalized_launch_type,
            "selected_aweme_id": selected_aweme_id if normalized_launch_type == UNI_AWEME_LAUNCH_TYPE else None,
            "products": products,
            "aweme_accounts": aweme_accounts,
            "balance": service.get_account_balance({"advertiser_id": advertiser_id}),
            "ad_quota": service.get_ad_quota({"advertiser_id": advertiser_id}),
            "instruction_to_agent": (
                "Select only officially returned assets, then check the locked launch "
                "authorization and preview the exact payload before any create call."
            ),
        }
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_get_account_report(
    ctx: McpContext,
    advertiser_id: int,
    start_date: str,
    end_date: str,
    marketing_goal: str = "ALL",
    order_platform: str = "QIANCHUAN",
    fields: list[str] | None = None,
    filtering: dict[str, Any] | None = None,
    group_by: list[str] | None = None,
    time_granularity: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """Get account-level reports for classic Qianchuan campaigns only.

    Full-domain account spend uses qianchuan_start_spend_report; breakdowns use qianchuan_get_uni_promotion_report;
    this endpoint does not cover their spend. An empty result is not zero spend.
    Distinguish no data for the requested dates, no active ads, and a reporting
    scope mismatch (campaign type, marketing_goal, filters or dimensions) using
    the relevant plan list/detail and report configuration before concluding.
    """
    query = _report_query(
        advertiser_id=advertiser_id,
        start_date=start_date,
        end_date=end_date,
        marketing_goal=marketing_goal,
        order_platform=order_platform,
        fields=fields or list(DEFAULT_ACCOUNT_REPORT_FIELDS),
        filtering=filtering,
        group_by=group_by,
        time_granularity=time_granularity,
        page=page,
        page_size=page_size,
    )
    return _read_call(ctx, lambda service: service.get_account_report(query))


@mcp.tool()
def qianchuan_get_ad_report(
    ctx: McpContext,
    advertiser_id: int,
    start_date: str,
    end_date: str,
    marketing_goal: str = "ALL",
    order_platform: str = "QIANCHUAN",
    fields: list[str] | None = None,
    filtering: dict[str, Any] | None = None,
    ad_ids: list[int] | None = None,
    group_by: list[str] | None = None,
    time_granularity: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """Get ad-level reports for classic Qianchuan campaigns only.

    Full-domain (uni-promotion) campaigns use qianchuan_get_uni_promotion_report;
    this endpoint does not cover their spend. An empty result is not zero spend.
    Distinguish no data for the requested dates, no active ads, and a reporting
    scope mismatch (campaign type, marketing_goal, filters or dimensions) using
    the relevant plan list/detail and report configuration before concluding.
    """
    query = _report_query(
        advertiser_id=advertiser_id,
        start_date=start_date,
        end_date=end_date,
        marketing_goal=marketing_goal,
        order_platform=order_platform,
        fields=fields,
        filtering=filtering,
        ad_ids=ad_ids,
        group_by=group_by,
        time_granularity=time_granularity,
        page=page,
        page_size=page_size,
    )
    return _read_call(ctx, lambda service: service.get_ad_report(query))


@mcp.tool()
def qianchuan_get_material_report(
    ctx: McpContext,
    advertiser_id: int,
    start_date: str,
    end_date: str,
    marketing_goal: str = "ALL",
    order_platform: str = "QIANCHUAN",
    fields: list[str] | None = None,
    filtering: dict[str, Any] | None = None,
    ad_ids: list[int] | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """Get Qianchuan material report data for creative analysis."""
    query = _report_query(
        advertiser_id=advertiser_id,
        start_date=start_date,
        end_date=end_date,
        marketing_goal=marketing_goal,
        order_platform=order_platform,
        fields=fields,
        filtering=filtering,
        ad_ids=ad_ids,
        page=page,
        page_size=page_size,
    )
    return _read_call(ctx, lambda service: service.get_material_report(query))


@mcp.tool()
def qianchuan_get_search_word_report(
    ctx: McpContext,
    advertiser_id: int,
    start_date: str,
    end_date: str,
    marketing_goal: str = "ALL",
    order_platform: str = "QIANCHUAN",
    fields: list[str] | None = None,
    filtering: dict[str, Any] | None = None,
    ad_ids: list[int] | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """Get Qianchuan search word report data."""
    query = _report_query(
        advertiser_id=advertiser_id,
        start_date=start_date,
        end_date=end_date,
        marketing_goal=marketing_goal,
        order_platform=order_platform,
        fields=fields,
        filtering=filtering,
        ad_ids=ad_ids,
        page=page,
        page_size=page_size,
    )
    return _read_call(ctx, lambda service: service.get_search_word_report(query))


@mcp.tool()
def qianchuan_check_keywords(
    ctx: McpContext,
    advertiser_id: int,
    keywords: list[str],
) -> dict[str, Any]:
    """Validate keywords through the official non-mutating POST endpoint."""
    normalized = list(dict.fromkeys(item.strip() for item in keywords if item.strip()))
    if not normalized:
        raise ToolError("keywords must contain at least one non-empty value.")
    return _read_call(
        ctx,
        lambda service: service.check_keywords(
            {"advertiser_id": advertiser_id, "keywords": normalized}
        ),
    )


@mcp.tool()
def qianchuan_get_uni_promotion_report(
    ctx: McpContext,
    advertiser_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
    marketing_goal: str = "ALL",
    order_platform: str = "QIANCHUAN",
    fields: list[str] | None = None,
    filtering: dict[str, Any] | None = None,
    ad_ids: list[int] | None = None,
    group_by: list[str] | None = None,
    time_granularity: str | None = None,
    page: int = 1,
    page_size: int = 100,
    data_topic: str | None = None,
    data_period: str = "ALL_DATA",
    dimensions: list[str] | None = None,
    metrics: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    order_by: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """高级全域细分报表，不是查询账户总消耗的首选工具。

    总消耗请用qianchuan_start_spend_report。本工具用于单个主题的项目/商品/素材
    细分；先调用配置接口选择主题对应指标。OVERALL_ROI_*的整体消耗通常是
    stat_cost_for_roi2，不要盲填stat_cost。不同主题/维度不可相加，否则重复计数。
    商品全域不等于纯商品卡流量。旧无data_topic路径兼容保留，不推荐新查询使用。

    For current APIs, first call qianchuan_get_uni_promotion_report_config, then pass
    data_topic, dimensions, metrics, filters, start_time, end_time, and order_by. The
    older start_date/end_date arguments remain for compatibility with legacy clients.

    Use this for full-domain (uni-promotion) campaigns; classic account/ad reports
    do not cover them. An empty result is not zero spend: distinguish no data for
    the requested dates, no active ads, and mismatched topics, filters or dimensions
    using full-domain plan list/detail and the returned report configuration.
    Do not substitute classic reports when this query returns no data.
    """
    if data_topic:
        resolved_start_time = start_time or (
            f"{start_date} 00:00:00" if start_date else None
        )
        resolved_end_time = end_time or (f"{end_date} 23:59:59" if end_date else None)
        missing = [
            name
            for name, value in (
                ("dimensions", dimensions),
                ("metrics", metrics),
                ("filters", filters),
                ("start_time", resolved_start_time),
                ("end_time", resolved_end_time),
                ("order_by", order_by),
            )
            if value is None
        ]
        if missing:
            raise ToolError(
                "Current uni-promotion reports require: " + ", ".join(missing)
            )
        params = {
            "advertiser_id": advertiser_id,
            "data_topic": data_topic,
            "data_period": data_period,
            "dimensions": dimensions,
            "metrics": metrics,
            "filters": filters,
            "start_time": resolved_start_time,
            "end_time": resolved_end_time,
            "order_by": order_by,
            "page": page,
            "page_size": page_size,
        }
        return _read_call(
            ctx,
            lambda service: service.get_uni_promotion_report_data(params),
        )
    if not start_date or not end_date:
        raise ToolError(
            "Provide data_topic and the current report fields, or both start_date and end_date."
        )
    query = _report_query(
        advertiser_id=advertiser_id,
        start_date=start_date,
        end_date=end_date,
        marketing_goal=marketing_goal,
        order_platform=order_platform,
        fields=fields,
        filtering=filtering,
        ad_ids=ad_ids,
        group_by=group_by,
        time_granularity=time_granularity,
        page=page,
        page_size=page_size,
    )
    return _read_call(ctx, lambda service: service.get_uni_promotion_report(query))


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
def qianchuan_get_live_uni_report_config(ctx: McpContext, advertiser_id: int) -> dict[str, Any]:
    """Read official live UNI dimensions/metrics. No ad writes; OAuth may refresh.

    Use returned fields and filter operators for qianchuan_get_live_uni_report.
    These are live account/material topics, not standard or product reports.
    """
    from app.live_read import TOPICS, identity
    identity(advertiser_id, 1)
    return _read_call(ctx, lambda service: service.get_uni_promotion_report_config(
        {"advertiser_id": advertiser_id, "data_topics": TOPICS, "data_period": "ALL_DATA"}))


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
def qianchuan_get_live_uni_report(ctx: McpContext, query: dict[str, Any]) -> dict[str, Any]:
    """Read one live full-domain report page, never fall back to standard reports.

    Required query: advertiser_id, data_topic, dimensions, metrics, filters,
    start_time/end_time (YYYY-MM-DD HH:MM:SS, account reporting time), order_by.
    Optional page/page_size. First obtain official live report config; do not
    invent metrics or filter operators. Topics are OVERALL_ROI_LIVE_AWEME,
    OVERALL_ROI_LIVE_MATERIAL_LIVE, OVERALL_ROI_LIVE_MATERIAL_VIDEO.
    Empty results are not evidence of zero spend. Preserve official pagination,
    units, attribution definitions and request_id. OAuth may refresh.
    """
    from app.live_read import LiveReport
    try:
        params = LiveReport.model_validate(query).model_dump()
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    params["data_period"] = "ALL_DATA"
    return _read_call(ctx, lambda service: service.get_uni_promotion_report_data(params))


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
def qianchuan_get_live_uni_diagnostics(ctx: McpContext, advertiser_id: int, ad_id: int,
                                      page: int = 1, page_size: int = 100) -> dict[str, Any]:
    """Read exact live plan status and one page of official audit suggestions.

    Verify ad_id and LIVE_PROM_GOODS from detail before reading suggestions.
    Never changes budget/ROI/status. Not a complete delivery diagnosis; missing
    suggestions do not prove eligibility or healthy delivery. OAuth may refresh.
    """
    from app.live_read import diagnose
    return _read_call(ctx, lambda service: diagnose(service, advertiser_id, ad_id, page, page_size))


@mcp.tool()
def qianchuan_get_uni_promotion_report_config(
    ctx: McpContext,
    advertiser_id: int,
    data_topics: list[str],
    data_period: str = "ALL_DATA",
) -> dict[str, Any]:
    """查询一个业务粒度的官方主题维度/指标配置，不查询实际消耗。

    普通日期/账户总消耗直接用qianchuan_start_spend_report，不必扫描所有主题。
    OVERALL_ROI_PRODUCT_*用于商品全域，OVERALL_ROI_LIVE_*用于直播全域；
    SITE_PROMOTION_*也必须按返回配置确认指标和适用粒度，不混用指标，不跨主题求和。

    Useful topics for product full-domain diagnosis include SITE_PROMOTION_PRODUCT_AD,
    SITE_PROMOTION_PRODUCT_PRODUCT, and SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO.
    """
    if not data_topics:
        raise ToolError("data_topics must contain at least one official topic enum.")
    return _read_call(
        ctx,
        lambda service: service.get_uni_promotion_report_config(
            {
                "advertiser_id": advertiser_id,
                "data_topics": list(dict.fromkeys(data_topics)),
                "data_period": data_period,
            }
        ),
    )


@mcp.tool()
def qianchuan_suggest_roi_goal(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Get Qianchuan suggested ROI goal from the official suggestion API."""
    return _read_call(ctx, lambda service: service.suggest_roi_goal(params))


@mcp.tool()
def qianchuan_suggest_budget(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Get Qianchuan suggested budget from the official suggestion API."""
    return _read_call(ctx, lambda service: service.suggest_budget(params))


@mcp.tool()
def qianchuan_estimate_effect(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Estimate delivery effect through the official Qianchuan estimate API."""
    return _read_call(ctx, lambda service: service.estimate_effect(params))


@mcp.tool()
def qianchuan_create_campaign(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create a Qianchuan campaign after account policy and explicit confirmation."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(ctx, "tool.qianchuan_campaign_create_v1", request, "create_campaign")


@mcp.tool()
def qianchuan_update_campaign(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update a Qianchuan campaign after account policy and explicit confirmation."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(ctx, "tool.qianchuan_campaign_update_v1", request, "update_campaign")


@mcp.tool()
def qianchuan_get_campaigns(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """List Qianchuan campaigns."""
    return _read_call(ctx, lambda service: service.get_campaigns(params))


@mcp.tool()
def qianchuan_create_ad(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create a Qianchuan ad with creative rules after explicit confirmation."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(ctx, "tool.qianchuan_ad_create_v1", request, "create_ad")


@mcp.tool()
def qianchuan_update_ad(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update a Qianchuan ad after account policy and explicit confirmation."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(ctx, "tool.qianchuan_ad_update_v1", request, "update_ad")


def _guarded_payload_write(
    ctx: McpContext,
    *,
    tool_key: str,
    method_name: str,
    payload: dict[str, Any],
    confirm: bool,
    reason: str | None,
) -> dict[str, Any]:
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(ctx, f"tool.{tool_key}", request, method_name)


@mcp.tool()
def qianchuan_update_account_budget(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update an account budget using the official payload and dedicated policy permission."""
    return _guarded_payload_write(
        ctx,
        tool_key="qianchuan_account_budget_update_v1",
        method_name="update_account_budget",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_update_ad_overall_marketing(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Legacy alias. Prefer qianchuan_upgrade_uni_promotion_to_multiplier for PC full-domain plans."""
    return _guarded_payload_write(
        ctx,
        tool_key="qianchuan_ad_overall_marketing_update_v1",
        method_name="update_ad_overall_marketing",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_upgrade_uni_promotion_to_multiplier(
    ctx: McpContext,
    advertiser_id: int,
    ad_id: int,
    roi2_goal: float,
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Upgrade one paused PC full-domain plan to a multiplier plan.

    Uses POST /v1.0/qianchuan/ad/overall_marketing/update/ with the exact official
    payload {advertiser_id, ad_id_list:[{ad_id, roi2_goal}]}. The wrapper reads
    official plan detail first, requires DISABLE, and submits exactly one plan.
    """
    request = ToolCallRequest(
        payload={
            "advertiser_id": advertiser_id,
            "ad_id_list": [{"ad_id": ad_id, "roi2_goal": roi2_goal}],
        },
        confirm=confirm,
        reason=reason,
    )
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_multiplier_upgrade_v1",
        request,
        "upgrade_uni_promotion_to_multiplier",
    )


@mcp.tool()
def qianchuan_update_ad_region(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update official region targeting for an existing standard ad."""
    return _guarded_payload_write(
        ctx,
        tool_key="qianchuan_ad_region_update_v1",
        method_name="update_ad_region",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_update_ad_schedule_date(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update official start/end schedule dates for an existing standard ad."""
    return _guarded_payload_write(
        ctx,
        tool_key="qianchuan_ad_schedule_date_update_v1",
        method_name="update_ad_schedule_date",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_update_ad_schedule_fixed_range(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update official fixed delivery hours for an existing standard ad."""
    return _guarded_payload_write(
        ctx,
        tool_key="qianchuan_ad_schedule_fixed_range_update_v1",
        method_name="update_ad_schedule_fixed_range",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_update_campaign_status_batch(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Enable/disable standard campaigns in batch; destructive status values are blocked."""
    return _guarded_payload_write(
        ctx,
        tool_key="qianchuan_batch_campaign_status_update_v1",
        method_name="update_campaign_status_batch",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_get_ads(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """List Qianchuan ads."""
    return _read_call(ctx, lambda service: service.get_ads(params))


@mcp.tool()
def qianchuan_get_ad_detail(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """Get Qianchuan ad detail and creative rules."""
    return _read_call(ctx, lambda service: service.get_ad_detail(params))


@mcp.tool()
def qianchuan_get_live_delivery_capabilities() -> dict[str, Any]:
    """Offline capability report for live advertising; no account or network access."""
    return {"marketing_goal": "LIVE_PROM_GOODS", "offline_diagnosis": True,
            "standard_live_report": "qianchuan_get_live_ad_report",
            "uni_live_report_config": "qianchuan_get_live_uni_report_config",
            "uni_live_report": "qianchuan_get_live_uni_report",
            "uni_live_audit_diagnostics": "qianchuan_get_live_uni_diagnostics",
            "uni_live_creation": "sdk_subset_implemented_default_disabled",
            "live_integration_tested": False,
            "message": "直播创建已按SDK实现受限子集，独立开关默认关闭，未经实测；标准报表不可冒充全域报表。"}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
def qianchuan_get_live_uni_create_contract() -> dict[str, Any]:
    """Get SDK-derived live UNI input schema offline. Default-disabled; not live tested."""
    from app.live_contract import contract
    return contract()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
def qianchuan_validate_live_uni_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """OFFLINE schema validation only; no token, inventory, balance or account calls.

    This is not a ready-for-execution preview and does not grant authority to create.
    """
    from app.live_contract import validate_live
    try:
        validated = validate_live(payload)
    except PydanticValidationError as exc:
        return {"status": "invalid_input", "errors": [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()], "writes_submitted": False}
    return {"status": "schema_valid", "payload": validated, "official_preflight": False, "live_tested": False, "writes_submitted": False}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False))
def qianchuan_create_live_uni_ad(ctx: McpContext, payload: dict[str, Any], confirm: bool = False, reason: str | None = None) -> dict[str, Any]:
    """Create live PC UNI via SDK-derived subset, only after explicit live-create gate enabled.

    Runs live identity/authorization/balance checks and existing write policy/cooldown.
    Never auto-enables configuration; no retries. Not production-verified. Does not create
    standard Campaign or SXT order. Returned ad_id must be reconciled before further writes.
    """
    if not _settings(ctx).qianchuan_live_create_enabled:
        return {"status": "blocked", "reason": "live_create_disabled", "writes_submitted": False}
    from app.live_contract import validate_live
    try:
        validated = validate_live(payload)
    except PydanticValidationError as exc:
        raise ToolError("Invalid live payload; use offline live payload validator for field errors") from exc
    return qianchuan_create_uni_aweme_ad(ctx, validated, confirm, reason)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
def qianchuan_analyze_live_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Analyze supplied live-plan facts OFFLINE without reading accounts or writing ads.

    Required fields: advertiser_id, ad_id, marketing_goal=LIVE_PROM_GOODS,
    window_start/window_end (timezone-aware ISO datetime), stat_cost_yuan,
    pay_amount_yuan, paid_orders, attribution_mature (bool). Optional plan_status,
    audit_rejected. Money must be normalized yuan. Missing facts are not treated as zero.
    Returns findings, observed ROI and empty actions; never guarantees future ROI.
    """
    from app.live_delivery import analyze
    try:
        return analyze(snapshot)
    except (ValueError, TypeError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False))
def qianchuan_get_live_ad_report(ctx: McpContext, advertiser_id: int, ad_ids: list[int],
                                start_date: str, end_date: str, page: int = 1, page_size: int = 100) -> dict[str, Any]:
    """Read STANDARD Qianchuan LIVE_PROM_GOODS plan reports, excluding Aweme/SXT orders.

    Not a full-domain report endpoint. Explicit account, plan IDs and dates required.
    One page per call; returns original page_info and request_id. No advertising writes;
    OAuth token refresh may change local/gateway authorization state.
    """
    from app.live_delivery import report_params
    try:
        params = report_params(advertiser_id, start_date, end_date, ad_ids, page, page_size)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return _read_call(ctx, lambda service: service.call_tool(
        "qianchuan_report_ad_get_v1", ToolCallRequest(params=params)))


@mcp.tool()
def qianchuan_get_uni_aweme_create_contract() -> dict[str, Any]:
    """Return the guarded PC uni-aweme create payload contract and a smart-material example."""
    today = date.today()
    return {
        "endpoint": "/v1.0/qianchuan/uni_aweme/ad/create/",
        "local_launch_type": "UNI_AWEME",
        "required_top_level": [
            "advertiser_id",
            "aweme_id",
            "marketing_goal",
            "product_ids",
            "multi_product_creative_list",
            "programmatic_creative_media_list",
            "delivery_setting",
        ],
        "delivery_setting_required": [
            "budget",
            "smart_bid_type",
            "qcpx_mode",
            "video_schedule_type",
            "start_time",
            "end_time",
            "daily_delivery_time",
            "deep_external_action",
        ],
        "smart_bid_type_values": ["SMART_BID_CONSERVATIVE", "SMART_BID_CUSTOM"],
        "video_goods_product_fields": [
            "product_ids",
            "multi_product_creative_list",
        ],
        "multi_product_creative_item_required": [
            "product_id",
            "creative_type",
            "hide_in_aweme",
        ],
        "multi_product_creative_item_allowed": [
            "product_id",
            "creative_type",
            "hide_in_aweme",
            "carousel_material",
        ],
        "graphic_carousel_contract": {
            "field": "multi_product_creative_list[].carousel_material",
            "official_fields": ["aweme_carousel_id", "carousel_id"],
            "reference_rule": (
                "Use exactly one existing official carousel identity: aweme_carousel_id "
                "or carousel_id. material_id is read/report metadata and is not valid here."
            ),
            "read_before_write": [
                "qianchuan_get_carousels",
                "qianchuan_get_aweme_carousels",
            ],
        },
        "video_goods_omit_fields": [
            "creative_setting",
            "product_channel_info",
            "uni_product_info",
            "product_infos",
            "products",
        ],
        "conservative_bid_forbidden_delivery_fields": ["roi2_goal"],
        "never_send": ["marketing_scene", "campaign_scene", "launch_type"],
        "minimal_video_goods_example": {
            "advertiser_id": 1811432276115675,
            "aweme_id": 3958659109113744,
            "name": "PC全域单商品测试",
            "marketing_goal": "VIDEO_PROM_GOODS",
            "product_ids": [3825578976894648335],
            "multi_product_creative_list": [
                {
                    "product_id": 3825578976894648335,
                    "creative_type": "PROGRAMMATIC_CREATIVE",
                    "hide_in_aweme": True,
                }
            ],
            "programmatic_creative_media_list": {
                "block_video_material": [],
                "title_material": [],
                "video_material": [],
            },
            "delivery_setting": {
                "budget": 300,
                "smart_bid_type": "SMART_BID_CUSTOM",
                "roi2_goal": 50,
                "qcpx_mode": "QCPX_MODE_ON",
                "video_schedule_type": "SCHEDULE_FROM_NOW",
                "start_time": today.isoformat(),
                "end_time": (today + timedelta(days=2)).isoformat(),
                "daily_delivery_time": 0.5,
                "deep_external_action": "AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI",
                "enable_aigc_creative": True,
            },
        },
        "notes": [
            "The create wrapper sends payload fields unchanged after local safety checks.",
            "Current VIDEO_PROM_GOODS creation sends both product_ids and the official multi_product_creative_list structure; a live product_ids-only request was rejected with 商品全域计划请使用多品结构.",
            "The verified automatic-material request also sends programmatic_creative_media_list with three empty arrays; do not create placeholder video rows with aweme_item_id=0.",
            "Product full-domain supports carousel (图文) creatives in multi_product_creative_list[].carousel_material. Product-card image_material is not a carousel creative and remains a separate 750x750 card capability.",
            "The verified delivery contract includes QCPX_MODE_ON, video SCHEDULE_FROM_NOW, dates, daily_delivery_time>=0.5, and AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI.",
            "Official support confirmed that creative_setting belongs to live full-domain rather than product full-domain, and that undocumented multi_product_creative_list children such as creative_card plus product_channel_info must be omitted.",
            "SMART_BID_CONSERVATIVE must omit roi2_goal.",
            "The minimal creative item is derived from both manually created plans 1870838646493611 and 1870839628901003: product_id, PROGRAMMATIC_CREATIVE, and hide_in_aweme=true; response-only null/empty children are not replayed.",
            "This complete contract created plan 1870853765929163 successfully and the wrapper immediately verified DISABLE. Ocean Engine normalized enable_aigc_creative to false in detail, so never claim AIGC is enabled without readback.",
            "Preview and create use the same product, budget, Aweme, and inventory validation.",
            "CREATE-scene eligibility requires authorization, shop permission, and no product full-domain disablement. SELF accounts do not require can_control_uniprom=true; delegated accounts do.",
            "If control is unavailable, query qianchuan_get_uni_authorizable_shops and obtain explicit user approval before qianchuan_apply_uni_promotion_authorization.",
            "A successful official response contains data.ad_id.",
        ],
    }


@mcp.tool()
def qianchuan_apply_uni_promotion_authorization(
    ctx: McpContext,
    advertiser_id: int,
    aweme_ids: list[int],
    marketing_goal: str,
    shop_id: int,
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Apply for scoped product full-domain control; requires policy permission and confirmation."""
    _ensure_autonomous_advertiser(ctx, advertiser_id)
    payload = {
        "advertiser_id": advertiser_id,
        "aweme_ids": aweme_ids,
        "marketing_goal": marketing_goal,
        "shop_id": shop_id,
    }
    try:
        with _db_session(ctx) as db:
            authorization = PersistenceService(db).get_launch_authorization(advertiser_id)
            if authorization is None or not authorization.locked:
                raise ValidationError("Locked launch authorization is required before UNI authorization.")
            if marketing_goal not in set(authorization.allowed_marketing_goals or []):
                raise ValidationError("marketing_goal is outside locked launch authorization.")
            if UNI_AWEME_LAUNCH_TYPE not in set(authorization.allowed_marketing_scenes or []):
                raise ValidationError("UNI_AWEME is outside locked launch authorization.")
            blocked_aweme_ids = {
                str(item) for item in aweme_ids
            } - set(authorization.allowed_aweme_ids or [])
            if not aweme_ids or blocked_aweme_ids:
                raise ValidationError(
                    "aweme_ids are empty or outside locked launch authorization: "
                    + ", ".join(sorted(blocked_aweme_ids))
                )
        shops = _strategy_service(ctx).get_uni_authorizable_shops(
            {"advertiser_id": advertiser_id, "page": 1, "page_size": 100}
        )
        rows = ((shops.get("data") or {}).get("shop_list") or [])
        if str(shop_id) not in {
            str(row.get("shop_id")) for row in rows if isinstance(row, dict)
        }:
            raise ValidationError("shop_id was not returned by the official authorizable-shop API.")
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_authorization_apply_v1",
        request,
        "apply_uni_promotion_authorization",
    )


@mcp.tool()
def qianchuan_initialize_uni_promotion_authorization(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Initialize official product full-domain authorization after explicit permission."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_auth_init_v1",
        request,
        "initialize_uni_promotion_authorization",
    )


@mcp.tool()
def qianchuan_create_uni_aweme_ad(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create one PC uni-aweme plan using the exact contract from get_uni_aweme_create_contract."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(ctx, "tool.qianchuan_uni_aweme_ad_create_v1", request, "create_uni_aweme_ad")


@mcp.tool()
def qianchuan_update_uni_aweme_ad(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update a PC uni-aweme plan using only fields supported by the official endpoint."""
    return _guarded_payload_write(
        ctx,
        tool_key="qianchuan_uni_aweme_ad_update_v1",
        method_name="update_uni_aweme_ad",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_get_uni_promotions(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """List PC uni-promotion plans for discovery, post-create reconciliation, and duplicate checks."""
    return _read_call(ctx, lambda service: service.get_uni_promotions(params))


@mcp.tool()
def qianchuan_get_uni_promotion_ad_detail(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Get the official detail of a PC Qianchuan uni-promotion ad."""
    return _read_call(ctx, lambda service: service.get_uni_promotion_ad_detail(params))


@mcp.tool()
def qianchuan_get_uni_aweme_video_candidates(
    ctx: McpContext,
    advertiser_id: int,
    ad_id: int,
    aweme_id: int,
    product_id: int,
    marketing_goal: str = "VIDEO_PROM_GOODS",
    cursor: int = 0,
    count: int = 50,
) -> dict[str, Any]:
    """List read-only Aweme video candidates after verifying the exact PC uni plan.

    This tool never binds material or changes plan status. exact_product_candidates have
    official product_info matching product_id; unlinked_candidates require manual review.
    """
    return _read_call(
        ctx,
        lambda service: service.get_uni_aweme_video_candidates(
            advertiser_id=advertiser_id,
            ad_id=ad_id,
            aweme_id=aweme_id,
            product_id=product_id,
            marketing_goal=marketing_goal,
            cursor=cursor,
            count=count,
        ),
    )


@mcp.tool()
def qianchuan_get_uni_aweme_carousel_candidates(
    ctx: McpContext,
    advertiser_id: int,
    ad_id: int,
    aweme_id: int,
    product_id: int,
    marketing_goal: str = "VIDEO_PROM_GOODS",
    cursor: int = 0,
    count: int = 50,
) -> dict[str, Any]:
    """List product-specific published 图文 candidates after verifying the exact PC uni plan.

    This read-only tool always sends the official ``filtering.product_id`` shape.
    Its returned aweme_carousel_id may be used by the guarded carousel bind tool;
    material_id is report metadata only and must not be written into a plan payload.
    """
    return _read_call(
        ctx,
        lambda service: service.get_uni_aweme_carousel_candidates(
            advertiser_id=advertiser_id,
            ad_id=ad_id,
            aweme_id=aweme_id,
            product_id=product_id,
            marketing_goal=marketing_goal,
            cursor=cursor,
            count=count,
        ),
    )


@mcp.tool()
def qianchuan_get_uni_promotion_products(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """List products available for PC Qianchuan uni-promotion."""
    return _read_call(ctx, lambda service: service.get_uni_promotion_products(params))


@mcp.tool()
def qianchuan_get_uni_promotion_product_aweme_accounts(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Get official Aweme accounts available for a uni-promotion product."""
    return _read_call(
        ctx,
        lambda service: service.get_uni_promotion_product_aweme_accounts(params),
    )


@mcp.tool()
def qianchuan_get_uni_aweme_suggested_budget(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Get the official suggested budget for a PC uni-aweme launch."""
    return _read_call(ctx, lambda service: service.get_uni_aweme_suggested_budget(params))


@mcp.tool()
def qianchuan_get_uni_aweme_suggested_roi(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Get the official suggested ROI for a PC uni-aweme launch."""
    return _read_call(ctx, lambda service: service.get_uni_aweme_suggested_roi(params))


@mcp.tool()
def qianchuan_get_uni_promotion_ad_suggestions(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Get official review and optimization suggestions for a uni-promotion plan."""
    return _read_call(ctx, lambda service: service.get_uni_promotion_ad_suggestions(params))


@mcp.tool()
def qianchuan_get_product_competition_analyses(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """List official Qianchuan product-competition analysis rows.

    Pass the official query fields in params, including advertiser_id. This tool is
    read-only and remains protected by the hard advertiser allowlist.
    """
    return _read_call(ctx, lambda service: service.get_product_competition_analyses(params))


@mcp.tool()
def qianchuan_get_product_competition_stats_comparison(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Get official effect/statistics comparison data for selected products (read-only)."""
    return _read_call(
        ctx,
        lambda service: service.get_product_competition_stats_comparison(params),
    )


@mcp.tool()
def qianchuan_get_product_competition_creative_comparison(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Get official creative comparison data for selected products (read-only)."""
    return _read_call(
        ctx,
        lambda service: service.get_product_competition_creative_comparison(params),
    )


@mcp.tool()
def qianchuan_update_uni_promotion_ad_budget(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update budget with payload {advertiser_id, update_budget_infos:[{ad_id,budget,previous_budget}]}.

    previous_budget is required when the account policy limits the change rate. It is used
    only by the local safety guard and is removed before the official API request.
    """
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_budget_update_v1",
        request,
        "update_uni_promotion_ad_budget",
    )


@mcp.tool()
def qianchuan_update_uni_promotion_ad_roi2_goal(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update ROI2 with payload {advertiser_id, update_roi2_infos:[{ad_id,roi2_goal}]}.

    Use only for an official detail state that supports ROI2 goals, such as applicable
    SMART_BID_CUSTOM plans. SMART_BID_CONSERVATIVE does not accept roi2_goal. Use fresh
    performance data and the locked strategy boundaries; do not jump directly to the
    final ROI target when the plan is still collecting conversion samples.
    """
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_roi2_goal_update_v1",
        request,
        "update_uni_promotion_ad_roi2_goal",
    )


@mcp.tool()
def qianchuan_update_uni_promotion_ad_status(
    ctx: McpContext,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Enable/disable with payload {advertiser_id, ad_ids:[...], opt_status}; DELETE is blocked.

    The canonical write body is ``payload``. ``params`` is accepted only as a
    backwards-compatible alias for the three status fields, because earlier
    unattended clients incorrectly placed the body there. Every call reads the
    official plan detail first; already-matching plans are skipped rather than
    written again.
    """
    request = ToolCallRequest(
        payload=_normalize_status_write_payload(payload, params),
        confirm=confirm,
        reason=reason,
    )
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_status_update_v1",
        request,
        "update_uni_promotion_ad_status",
    )


@mcp.tool()
def qianchuan_update_uni_promotion_ad_name(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update a PC uni-promotion plan name through the dedicated guarded endpoint."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_name_update_v1",
        request,
        "update_uni_promotion_ad_name",
    )


@mcp.tool()
def qianchuan_update_uni_promotion_ad_schedule(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update a PC uni-promotion plan schedule through the guarded endpoint."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_schedule_date_update_v1",
        request,
        "update_uni_promotion_ad_schedule",
    )


@mcp.tool()
def qianchuan_delete_uni_promotion_ad(
    ctx: McpContext,
    advertiser_id: int,
    ad_id: int,
    expected_name: str,
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Delete one paused full-domain plan after exact ID/name matching.

    This destructive tool is only for a user's explicit per-plan request. Autonomous
    workflows must continue to use reversible ENABLE/DISABLE status changes.
    """
    request = ToolCallRequest(
        payload={
            "advertiser_id": advertiser_id,
            "ad_ids": [ad_id],
            "opt_status": "DELETE",
            "expected_name": expected_name,
        },
        confirm=confirm,
        reason=reason,
    )
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_delete_guarded_v1",
        request,
        "delete_uni_promotion_ad",
    )


@mcp.tool()
def qianchuan_create_aweme_order(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create a Qianchuan aweme order after account policy and explicit confirmation."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(ctx, "tool.qianchuan_aweme_order_create_v1", request, "create_aweme_order")


@mcp.tool()
def qianchuan_create_aweme_uni_promotion_order(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create an aweme uni-promotion order after account policy and explicit confirmation."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_aweme_uni_promotion_order_create_v1",
        request,
        "create_aweme_uni_promotion_order",
    )


@mcp.tool()
def qianchuan_add_aweme_order_budget(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Add budget to an existing Aweme order through the official guarded endpoint."""
    return _guarded_payload_write(
        ctx,
        tool_key="qianchuan_aweme_order_budget_add_v1",
        method_name="add_aweme_order_budget",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_add_aweme_uni_promotion_order_budget(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Add budget to an existing Aweme uni-promotion order through a guarded endpoint."""
    return _guarded_payload_write(
        ctx,
        tool_key="qianchuan_aweme_uni_promotion_order_budget_add_v1",
        method_name="add_aweme_uni_promotion_order_budget",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_set_smart_boost(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Set official smart-boost optimization after dedicated policy authorization."""
    return _guarded_payload_write(
        ctx,
        tool_key="qianchuan_tools_smart_boost_ad_boost_set_v1",
        method_name="set_smart_boost",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_create_uni_promotion_control_task(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create a uni-promotion control task after account policy and explicit confirmation."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_control_task_create_v1",
        request,
        "create_uni_promotion_control_task",
    )


@mcp.tool()
def qianchuan_create_uni_promotion_smart_control_task(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create a uni-promotion smart control task after explicit confirmation."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_control_task_smart_control_create_v1",
        request,
        "create_uni_promotion_smart_control_task",
    )


@mcp.tool()
def qianchuan_get_uni_promotion_control_tasks(
    ctx: McpContext,
    params: dict[str, Any],
) -> dict[str, Any]:
    """List PC uni-promotion control tasks."""
    return _read_call(ctx, lambda service: service.get_uni_promotion_control_tasks(params))


def _control_task_write(
    ctx: McpContext,
    *,
    tool_key: str,
    method_name: str,
    payload: dict[str, Any],
    confirm: bool,
    reason: str | None,
) -> dict[str, Any]:
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(ctx, f"tool.{tool_key}", request, method_name)


@mcp.tool()
def qianchuan_update_uni_promotion_control_task(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update settings of an existing uni-promotion control task."""
    return _control_task_write(
        ctx,
        tool_key="qianchuan_uni_promotion_ad_control_task_update_v1",
        method_name="update_uni_promotion_control_task",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_update_uni_promotion_control_task_status(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update the reversible status of a uni-promotion control task."""
    return _control_task_write(
        ctx,
        tool_key="qianchuan_uni_promotion_ad_control_task_status_update_v1",
        method_name="update_uni_promotion_control_task_status",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_update_uni_promotion_control_task_budget(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update a uni-promotion control-task budget through a guarded write."""
    return _control_task_write(
        ctx,
        tool_key="qianchuan_uni_promotion_ad_control_task_budget_update_v1",
        method_name="update_uni_promotion_control_task_budget",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_update_uni_promotion_control_task_duration(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update a uni-promotion control-task duration through a guarded write."""
    return _control_task_write(
        ctx,
        tool_key="qianchuan_uni_promotion_ad_control_task_duration_update_v1",
        method_name="update_uni_promotion_control_task_duration",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_update_uni_promotion_smart_control_task_status(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update the reversible status of a smart-control task."""
    return _control_task_write(
        ctx,
        tool_key="qianchuan_uni_promotion_ad_control_task_smart_control_status_update_v1",
        method_name="update_uni_promotion_smart_control_task_status",
        payload=payload,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_get_materials(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """Get Qianchuan material library items."""
    return _read_call(ctx, lambda service: service.get_materials(params))


@mcp.tool()
def qianchuan_get_material_ads(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """Get ads related to one or more Qianchuan materials."""
    return _read_call(ctx, lambda service: service.get_material_ads(params))


@mcp.tool()
def qianchuan_get_images(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """Get Qianchuan images."""
    return _read_call(ctx, lambda service: service.get_images(params))


@mcp.tool()
def qianchuan_get_carousels(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """List advertiser-level Qianchuan carousel (图文) materials.

    This is read-only. Use returned carousel_id (not report-only material_id) as
    input to guarded carousel bind/create tools; do not infer an ID from a picture file.
    """
    return _read_call(ctx, lambda service: service.get_carousels(params))


@mcp.tool()
def qianchuan_get_aweme_carousels(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """List published Aweme 图文 materials; params.filtering.product_id is required."""
    return _read_call(ctx, lambda service: service.get_aweme_carousels(params))


@mcp.tool()
def qianchuan_delete_images(
    ctx: McpContext,
    advertiser_id: int,
    image_ids: list[str],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Permanently delete 1-100 exact image-library IDs after explicit confirmation."""
    normalized = [str(item).strip() for item in image_ids if str(item).strip()]
    if not 1 <= len(normalized) <= 100 or len(set(normalized)) != len(normalized):
        raise ToolError("image_ids must contain 1 to 100 distinct non-empty image IDs.")
    request = ToolCallRequest(
        payload={"advertiser_id": advertiser_id, "image_ids": normalized},
        confirm=confirm,
        reason=reason,
    )
    return _write_tool_action(ctx, "tool.qianchuan_image_delete_v1", request, "delete_images")


@mcp.tool()
def qianchuan_get_videos(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """Read Qianchuan video materials, including verification by video_ids or signatures."""
    return _read_call(ctx, lambda service: service.get_videos(params))


@mcp.tool()
def qianchuan_upload_video(
    ctx: McpContext,
    advertiser_id: int,
    file_path: str,
    filename: str | None = None,
    is_aigc: bool = False,
    labels: list[str] | None = None,
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Upload one server-local video to the Qianchuan material library.

    file_path must be inside QIANCHUAN_VIDEO_UPLOAD_ROOT. This tool uploads and reads back
    the material only; it never binds the video to a product or plan and never enables a plan.
    Account Policy must explicitly allow material_upload.
    """
    if advertiser_id < 1:
        raise ToolError("advertiser_id must be positive.")
    normalized_labels = list(dict.fromkeys(item.strip() for item in (labels or []) if item.strip()))
    if len(normalized_labels) > 20 or any(len(item) > 50 for item in normalized_labels):
        raise ToolError("labels must contain at most 20 unique values of at most 50 characters.")
    settings = _settings(ctx)
    try:
        prepared = prepare_video_upload(
            file_path=file_path,
            upload_root=settings.qianchuan_video_upload_root,
            max_size_bytes=settings.qianchuan_video_upload_max_mb * 1024 * 1024,
            filename=filename,
        )
    except VideoUploadValidationError as exc:
        raise ToolError(str(exc)) from exc
    return _upload_video_action(
        ctx,
        advertiser_id=advertiser_id,
        prepared=prepared,
        is_aigc=is_aigc,
        labels=normalized_labels,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_upload_image(
    ctx: McpContext,
    advertiser_id: int,
    file_path: str,
    filename: str | None = None,
    is_aigc: bool = True,
    require_square_750: bool = False,
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Upload one server-local image to the Qianchuan material library.

    The image must be inside QIANCHUAN_IMAGE_UPLOAD_ROOT. Set require_square_750=true
    for a uni-promotion product-card image. This tool uploads only; it never binds
    material to an ad or changes its delivery state.
    """
    if advertiser_id < 1:
        raise ToolError("advertiser_id must be positive.")
    settings = _settings(ctx)
    try:
        prepared = prepare_image_upload(
            file_path=file_path,
            upload_root=settings.qianchuan_image_upload_root,
            max_size_bytes=int(settings.qianchuan_image_upload_max_mb * 1024 * 1024),
            filename=filename,
            required_dimensions=(750, 750) if require_square_750 else None,
        )
    except ImageUploadValidationError as exc:
        raise ToolError(str(exc)) from exc
    return _upload_image_action(
        ctx,
        advertiser_id=advertiser_id,
        prepared=prepared,
        is_aigc=is_aigc,
        confirm=confirm,
        reason=reason,
    )


@mcp.tool()
def qianchuan_create_carousel_material(
    ctx: McpContext,
    advertiser_id: int,
    image_ids: list[str],
    file_name: str | None = None,
    description: str | None = None,
    audio_id: str | None = None,
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Create one Qianchuan 图文素材库 entry from already-uploaded image IDs.

    This calls the official ``/2/carousel/create/`` interface and returns its
    ``carousel_id``. It does not accept product_id, ad_id, campaign_id, or
    delivery fields; it cannot bind material or alter any plan. Account Policy
    must explicitly allow ``carousel_create``. If ``allowed_material_ids`` is
    configured, every supplied image_id must be allowlisted.
    """
    try:
        payload = build_carousel_create_payload(
            advertiser_id=advertiser_id,
            image_ids=image_ids,
            file_name=file_name,
            description=description,
            audio_id=audio_id,
        )
    except CarouselLibraryValidationError as exc:
        raise ToolError(str(exc)) from exc
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_carousel_create_v2",
        request,
        "create_carousel_material",
    )


@mcp.tool()
def qianchuan_get_ad_materials(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """Get materials bound to a Qianchuan ad."""
    return _read_call(ctx, lambda service: service.get_ad_materials(params))


@mcp.tool()
def qianchuan_add_uni_promotion_ad_material(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Advanced compatibility binding interface; prefer qianchuan_add_uni_promotion_product_card_images.

    Do not use this tool for normal product-card images: its raw nested payload is
    easy to mis-associate across products. Keep it only for a user-supplied,
    documented official payload that the dedicated tool cannot represent.
    """
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_material_add_v1",
        request,
        "add_uni_promotion_ad_material",
    )


@mcp.tool()
def qianchuan_add_uni_promotion_product_card_images(
    ctx: McpContext,
    advertiser_id: int,
    ad_id: int,
    bindings: list[dict[str, Any]],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Bind uploaded 750x750 images as product-card creatives on one uni-promotion ad.

    Supply one binding per product: {product_id, title, image_ids}. The MCP constructs
    the official multi-product payload so one image cannot accidentally be placed under
    the wrong product or combined with other image IDs in one creative item.
    """
    try:
        payload = build_uni_promotion_product_card_binding_payload(
            advertiser_id=advertiser_id,
            ad_id=ad_id,
            bindings=bindings,
        )
    except ProductCardBindingValidationError as exc:
        raise ToolError(str(exc)) from exc
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_material_add_v1",
        request,
        "add_uni_promotion_ad_material",
    )


@mcp.tool()
def qianchuan_add_uni_promotion_carousel_materials(
    ctx: McpContext,
    advertiser_id: int,
    ad_id: int,
    bindings: list[dict[str, Any]],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Bind product-scoped carousel (图文) creatives to one PC uni-promotion plan.

    ``bindings`` contains one object per product: ``{product_id, carousels}``.
    A carousel must reference exactly one official ``aweme_carousel_id`` or
    ``carousel_id`` returned by a read tool. ``material_id`` is report metadata,
    not a writable reference. The wrapper rejects arbitrary nested fields, enforces
    account policy/material allowlists and uses normal confirmation/cooldown gates.
    """
    try:
        payload = build_uni_promotion_carousel_binding_payload(
            advertiser_id=advertiser_id,
            ad_id=ad_id,
            bindings=bindings,
        )
    except CarouselBindingValidationError as exc:
        raise ToolError(str(exc)) from exc
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_material_add_v1",
        request,
        "add_uni_promotion_ad_material",
    )


@mcp.tool()
def qianchuan_delete_uni_promotion_ad_material(
    ctx: McpContext,
    payload: dict[str, Any],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Delete material from a uni-promotion ad after explicit confirmation."""
    request = ToolCallRequest(payload=payload, confirm=confirm, reason=reason)
    return _write_tool_action(
        ctx,
        "tool.qianchuan_uni_promotion_ad_material_delete_v1",
        request,
        "delete_uni_promotion_ad_material",
    )


@mcp.tool()
def qianchuan_get_uni_promotion_ad_materials(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """Get materials bound to a uni-promotion ad."""
    return _read_call(ctx, lambda service: service.get_uni_promotion_ad_materials(params))


@mcp.tool()
def qianchuan_get_uni_promotion_ad_products(ctx: McpContext, params: dict[str, Any]) -> dict[str, Any]:
    """Get products bound to a uni-promotion ad."""
    return _read_call(ctx, lambda service: service.get_uni_promotion_ad_products(params))


@mcp.tool()
def qianchuan_diagnose_roi(
    ctx: McpContext,
    advertiser_id: int,
    start_date: str,
    end_date: str,
    target_roi: float | None = None,
    marketing_goal: str = "ALL",
    order_platform: str = "QIANCHUAN",
    fields: list[str] | None = None,
    filtering: dict[str, Any] | None = None,
    ad_ids: list[int] | None = None,
    min_spend: float | None = None,
    min_clicks: int | None = None,
    min_orders: int | None = None,
    max_ads: int = 100,
    gross_margin_rate: float | None = None,
    refund_rate: float = 0,
    extra_cost_rate: float = 0,
    roi_safety_margin_rate: float = 0.1,
) -> dict[str, Any]:
    """Diagnose ROI directly from Qianchuan ad report data."""
    try:
        request = RoiDiagnosisRequest(
            advertiser_id=advertiser_id,
            start_date=start_date,
            end_date=end_date,
            target_roi=_optional_decimal(target_roi),
            marketing_goal=marketing_goal,
            order_platform=order_platform,
            fields=fields or list(DEFAULT_ROI_REPORT_FIELDS),
            filtering=filtering or {},
            ad_ids=ad_ids or [],
            min_spend=_optional_decimal(min_spend),
            min_clicks=min_clicks,
            min_orders=min_orders,
            max_ads=max_ads,
            gross_margin_rate=_optional_decimal(gross_margin_rate),
            refund_rate=_decimal(refund_rate),
            extra_cost_rate=_decimal(extra_cost_rate),
            roi_safety_margin_rate=_decimal(roi_safety_margin_rate),
        )
        return _model_dump(_strategy_service(ctx).diagnose_roi(request))
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_analyze_material_fatigue(
    ctx: McpContext,
    advertiser_id: int,
    current_start_date: str,
    current_end_date: str,
    previous_start_date: str,
    previous_end_date: str,
    marketing_goal: str = "ALL",
    order_platform: str = "QIANCHUAN",
    fields: list[str] | None = None,
    filtering: dict[str, Any] | None = None,
    ad_ids: list[int] | None = None,
    min_cost: float = 50,
    min_impressions: int = 1000,
    ctr_drop_rate: float = 0.3,
    roi_drop_rate: float = 0.3,
    max_materials: int = 200,
    page_size: int = 100,
) -> dict[str, Any]:
    """Analyze material fatigue by comparing current and previous material report windows."""
    try:
        request = MaterialFatigueRequest(
            advertiser_id=advertiser_id,
            current_start_date=current_start_date,
            current_end_date=current_end_date,
            previous_start_date=previous_start_date,
            previous_end_date=previous_end_date,
            marketing_goal=marketing_goal,
            order_platform=order_platform,
            fields=fields or list(DEFAULT_MATERIAL_REPORT_FIELDS),
            filtering=filtering or {},
            ad_ids=ad_ids or [],
            min_cost=_decimal(min_cost),
            min_impressions=min_impressions,
            ctr_drop_rate=_decimal(ctr_drop_rate),
            roi_drop_rate=_decimal(roi_drop_rate),
            max_materials=max_materials,
            page_size=page_size,
        )
        return _model_dump(_strategy_service(ctx).analyze_material_fatigue(request))
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_sync_ad_snapshots(
    ctx: McpContext,
    advertiser_id: int,
    start_date: str,
    end_date: str,
    marketing_goal: str = "ALL",
    order_platform: str = "QIANCHUAN",
    fields: list[str] | None = None,
    filtering: dict[str, Any] | None = None,
    ad_ids: list[int] | None = None,
    page_size: int = 1000,
    max_pages_per_day: int = 10,
) -> dict[str, Any]:
    """Sync Qianchuan ad daily report rows into the local snapshot database."""
    try:
        request = SnapshotSyncRequest(
            advertiser_id=advertiser_id,
            start_date=start_date,
            end_date=end_date,
            marketing_goal=marketing_goal,
            order_platform=order_platform,
            fields=fields or list(DEFAULT_ROI_REPORT_FIELDS),
            filtering=filtering or {},
            ad_ids=ad_ids or [],
            page_size=page_size,
            max_pages_per_day=max_pages_per_day,
        )
        with _db_session(ctx) as db:
            response = PersistenceService(db).sync_snapshots(
                request,
                strategy_service=_strategy_service(ctx),
                operator_id=_operator_id(ctx),
            )
        return _model_dump(response)
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_list_ad_snapshots(
    ctx: McpContext,
    advertiser_id: int,
    date_from: str,
    date_to: str,
    ad_ids: list[str] | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """List local Qianchuan ad daily snapshots."""
    try:
        start = _parse_date(date_from, "date_from")
        end = _parse_date(date_to, "date_to")
        if start > end:
            raise ValidationError("date_from must be less than or equal to date_to.")
        with _db_session(ctx) as db:
            service = PersistenceService(db)
            items, total = service.list_snapshots(
                advertiser_id=advertiser_id,
                date_from=start,
                date_to=end,
                ad_ids=ad_ids,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            response = service.snapshot_response(
                items=items,
                total=total,
                page=page,
                page_size=page_size,
            )
        return _model_dump(response)
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_build_roi_strategy(
    ctx: McpContext,
    advertiser_id: int,
    start_date: str,
    end_date: str,
    target_roi: float | None = None,
    marketing_goal: str = "ALL",
    order_platform: str = "QIANCHUAN",
    fields: list[str] | None = None,
    filtering: dict[str, Any] | None = None,
    ad_ids: list[int] | None = None,
    min_spend: float | None = None,
    min_clicks: int | None = None,
    min_orders: int | None = None,
    max_ads: int = 100,
    persist_decision: bool = True,
    gross_margin_rate: float | None = None,
    refund_rate: float = 0,
    extra_cost_rate: float = 0,
    roi_safety_margin_rate: float = 0.1,
) -> dict[str, Any]:
    """Build and optionally persist an ROI strategy from local ad snapshots."""
    try:
        request = RoiStrategyRequest(
            advertiser_id=advertiser_id,
            start_date=start_date,
            end_date=end_date,
            target_roi=_optional_decimal(target_roi),
            marketing_goal=marketing_goal,
            order_platform=order_platform,
            fields=fields or list(DEFAULT_ROI_REPORT_FIELDS),
            filtering=filtering or {},
            ad_ids=ad_ids or [],
            min_spend=_optional_decimal(min_spend),
            min_clicks=min_clicks,
            min_orders=min_orders,
            max_ads=max_ads,
            persist_decision=persist_decision,
            gross_margin_rate=_optional_decimal(gross_margin_rate),
            refund_rate=_decimal(refund_rate),
            extra_cost_rate=_decimal(extra_cost_rate),
            roi_safety_margin_rate=_decimal(roi_safety_margin_rate),
        )
        with _db_session(ctx) as db:
            response = PersistenceService(db).build_roi_strategy(
                request,
                strategy_service=_strategy_service(ctx),
                operator_id=_operator_id(ctx),
            )
        return _model_dump(response)
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_list_roi_decisions(
    ctx: McpContext,
    advertiser_id: int | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """List persisted local ROI decisions."""
    try:
        with _db_session(ctx) as db:
            items, total = PersistenceService(db).list_decisions(
                advertiser_id=advertiser_id,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            response = {
                "items": [_model_dump(serialize_decision(item)) for item in items],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        return response
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_get_roi_decision(ctx: McpContext, decision_no: str) -> dict[str, Any]:
    """Get one persisted ROI decision by decision number."""
    try:
        with _db_session(ctx) as db:
            return _model_dump(serialize_decision(PersistenceService(db).get_decision(decision_no)))
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_upsert_account_policy(
    ctx: McpContext,
    advertiser_id: int,
    account_role: str,
    account_name: str | None = None,
    allowed_actions: list[str] | None = None,
    product_scope: dict[str, Any] | None = None,
    allowed_product_ids: list[str] | None = None,
    allowed_ad_ids: list[str] | None = None,
    allowed_material_ids: list[str] | None = None,
    target_roi: float | None = None,
    max_daily_budget_change_rate: float = 0.2,
    write_enabled: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create or update an account policy that controls what AI may do for one advertiser."""
    try:
        _ensure_mcp_admin_tools_enabled(ctx)
        request = AccountPolicyUpsertRequest(
            account_name=account_name,
            account_role=account_role,
            allowed_actions=allowed_actions,
            product_scope=product_scope or {},
            allowed_product_ids=allowed_product_ids or [],
            allowed_ad_ids=allowed_ad_ids or [],
            allowed_material_ids=allowed_material_ids or [],
            target_roi=_optional_decimal(target_roi),
            max_daily_budget_change_rate=_decimal(max_daily_budget_change_rate),
            write_enabled=write_enabled,
            notes=notes,
        )
        with _db_session(ctx) as db:
            policy = PersistenceService(db).upsert_account_policy(advertiser_id, request)
            return _model_dump(serialize_account_policy(policy))
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_get_account_policy(ctx: McpContext, advertiser_id: int) -> dict[str, Any]:
    """Read the AI account policy for one advertiser."""
    with _db_session(ctx) as db:
        policy = PersistenceService(db).get_account_policy(advertiser_id)
        if policy is None:
            raise ToolError(f"Account policy for advertiser {advertiser_id} was not found.")
        return _model_dump(serialize_account_policy(policy))


@mcp.tool()
def qianchuan_list_account_policies(ctx: McpContext) -> dict[str, Any]:
    """List all configured AI account policies."""
    with _db_session(ctx) as db:
        items = [serialize_account_policy(item) for item in PersistenceService(db).list_account_policies()]
    return {"items": [_model_dump(item) for item in items], "total": len(items)}


@mcp.tool()
def qianchuan_upsert_campaign_playbook(
    ctx: McpContext,
    playbook_key: str,
    account_role: str,
    objective: str,
    target_roi: float | None = None,
    min_orders: int = 1,
    scale_roi_multiplier: float = 1.2,
    cut_roi_multiplier: float = 0.75,
    max_budget_change_rate: float = 0.2,
    material_rules: dict[str, Any] | None = None,
    rules: dict[str, Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Create or update a strategy playbook used by AI delivery planning."""
    try:
        _ensure_mcp_admin_tools_enabled(ctx)
        request = CampaignPlaybookUpsertRequest(
            account_role=account_role,
            objective=objective,
            target_roi=_optional_decimal(target_roi),
            min_orders=min_orders,
            scale_roi_multiplier=_decimal(scale_roi_multiplier),
            cut_roi_multiplier=_decimal(cut_roi_multiplier),
            max_budget_change_rate=_decimal(max_budget_change_rate),
            material_rules=material_rules or {},
            rules=rules or {},
            enabled=enabled,
        )
        with _db_session(ctx) as db:
            playbook = PersistenceService(db).upsert_campaign_playbook(playbook_key, request)
            return _model_dump(serialize_campaign_playbook(playbook))
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_list_campaign_playbooks(ctx: McpContext) -> dict[str, Any]:
    """List configured strategy playbooks."""
    with _db_session(ctx) as db:
        items = [serialize_campaign_playbook(item) for item in PersistenceService(db).list_campaign_playbooks()]
    return {"items": [_model_dump(item) for item in items], "total": len(items)}


@mcp.tool()
def qianchuan_create_material_decision(
    ctx: McpContext,
    advertiser_id: int,
    material_id: str,
    status: str,
    recommended_action: str,
    reason: str,
    material_name: str | None = None,
    source_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist an AI decision about whether a material should be tested, scaled, rotated, or rejected."""
    try:
        request = MaterialDecisionCreateRequest(
            advertiser_id=advertiser_id,
            material_id=material_id,
            material_name=material_name,
            status=status,
            recommended_action=recommended_action,
            reason=reason,
            source_json=source_json or {},
        )
        with _db_session(ctx) as db:
            decision = PersistenceService(db).create_material_decision(
                request,
                operator_id=_operator_id(ctx),
            )
            return _model_dump(serialize_material_decision(decision))
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_list_material_decisions(
    ctx: McpContext,
    advertiser_id: int | None = None,
    statuses: list[str] | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """List AI material decisions."""
    with _db_session(ctx) as db:
        items, total = PersistenceService(db).list_material_decisions(
            advertiser_id=advertiser_id,
            statuses=statuses,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
    return {
        "items": [_model_dump(serialize_material_decision(item)) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@mcp.tool()
def qianchuan_create_strategy_review(
    ctx: McpContext,
    summary: str,
    advertiser_id: int | None = None,
    playbook_key: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    worked: list[str] | None = None,
    failed: list[str] | None = None,
    suggested_changes: list[dict[str, Any]] | None = None,
    next_checks: list[str] | None = None,
    confidence: float = 0.5,
    source_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist an AI strategy review so future planning can learn from prior results."""
    try:
        request = StrategyReviewCreateRequest(
            advertiser_id=advertiser_id,
            playbook_key=playbook_key,
            period_start=_optional_date(period_start),
            period_end=_optional_date(period_end),
            summary=summary,
            worked=worked or [],
            failed=failed or [],
            suggested_changes=suggested_changes or [],
            next_checks=next_checks or [],
            confidence=_decimal(confidence),
            source_json=source_json or {},
        )
        with _db_session(ctx) as db:
            review = PersistenceService(db).create_strategy_review(
                request,
                operator_id=_operator_id(ctx),
            )
            return _model_dump(serialize_strategy_review(review))
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_list_strategy_reviews(
    ctx: McpContext,
    advertiser_id: int | None = None,
    playbook_key: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """List recent AI strategy reviews."""
    with _db_session(ctx) as db:
        items, total = PersistenceService(db).list_strategy_reviews(
            advertiser_id=advertiser_id,
            playbook_key=playbook_key,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
    return {
        "items": [_model_dump(serialize_strategy_review(item)) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@mcp.tool()
def qianchuan_build_delivery_plan(
    ctx: McpContext,
    advertiser_id: int,
    start_date: str,
    end_date: str,
    target_roi: float | None = None,
    playbook_key: str | None = None,
    gross_margin_rate: float | None = None,
    refund_rate: float = 0,
    extra_cost_rate: float = 0,
    roi_safety_margin_rate: float = 0.1,
    max_ads: int = 100,
    persist_decision: bool = True,
) -> dict[str, Any]:
    """Build an AI delivery plan using account policy, playbook, material decisions, and recent reviews."""
    try:
        request = DeliveryPlanRequest(
            advertiser_id=advertiser_id,
            start_date=start_date,
            end_date=end_date,
            target_roi=_optional_decimal(target_roi),
            playbook_key=playbook_key,
            gross_margin_rate=_optional_decimal(gross_margin_rate),
            refund_rate=_decimal(refund_rate),
            extra_cost_rate=_decimal(extra_cost_rate),
            roi_safety_margin_rate=_decimal(roi_safety_margin_rate),
            max_ads=max_ads,
            persist_decision=persist_decision,
        )
        with _db_session(ctx) as db:
            plan = PersistenceService(db).build_delivery_plan(
                request,
                strategy_service=_strategy_service(ctx),
                operator_id=_operator_id(ctx),
                require_policy=_settings(ctx).qianchuan_require_account_policy_for_writes,
            )
        return _model_dump(plan)
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_get_autonomy_profile(
    ctx: McpContext,
    advertiser_id: int = 1811432276115675,
) -> dict[str, Any]:
    """Check the locked autonomy inputs. If missing, ask the user every returned question first."""
    try:
        _ensure_autonomous_advertiser(ctx, advertiser_id)
        with _db_session(ctx) as db:
            profile = PersistenceService(db).get_autonomy_profile(advertiser_id)
            if profile is None:
                return {
                    "status": "setup_required",
                    "advertiser_id": advertiser_id,
                    "instruction_to_agent": (
                        "Ask the user all required questions. Never infer values from reports, "
                        "defaults, or prior unrelated conversation. After the user answers, call "
                        "qianchuan_save_autonomy_profile once."
                    ),
                    "required_user_inputs": autonomy_profile_questions(),
                }
            return {
                "status": "configured",
                "instruction_to_agent": (
                    "Reuse this locked profile. Do not ask again and do not call the save tool "
                    "unless the user explicitly requests a change."
                ),
                "profile": _model_dump(serialize_autonomy_profile(profile)),
            }
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_get_launch_authorization(
    ctx: McpContext,
    advertiser_id: int = 1811432276115675,
) -> dict[str, Any]:
    """Get locked Agent launch boundaries; if missing, ask every returned question first."""
    try:
        _ensure_autonomous_advertiser(ctx, advertiser_id)
        with _db_session(ctx) as db:
            authorization = PersistenceService(db).get_launch_authorization(advertiser_id)
            if authorization is None:
                return {
                    "status": "setup_required",
                    "advertiser_id": advertiser_id,
                    "instruction_to_agent": (
                        "First call the read-only discovery tools, then ask the user all required "
                        "launch-boundary questions. Never infer financial limits or widen scope."
                    ),
                    "required_user_inputs": launch_authorization_questions(),
                }
            return {
                "status": "configured",
                "instruction_to_agent": (
                    "Reuse these locked launch boundaries. Do not ask again or change them unless "
                    "the user explicitly requests a modification."
                ),
                "authorization": _model_dump(
                    serialize_launch_authorization(authorization)
                ),
            }
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_save_launch_authorization(
    ctx: McpContext,
    allowed_product_ids: list[str],
    allowed_marketing_goals: list[str],
    allowed_marketing_scenes: list[str],
    max_initial_campaign_budget: float,
    max_initial_ad_budget: float,
    allowed_aweme_ids: list[str] | None = None,
    allowed_material_ids: list[str] | None = None,
    min_account_balance: float = 0,
    min_product_inventory: int = 1,
    max_campaigns_per_day: int = 1,
    max_ads_per_day: int = 1,
    allow_campaign_create: bool = False,
    allow_ad_create: bool = False,
    advertiser_id: int = 1811432276115675,
    user_requested_update: bool = False,
    user_request_summary: str | None = None,
) -> dict[str, Any]:
    """Lock explicit user-approved product, identity, budget, inventory, and creation limits."""
    try:
        _ensure_autonomous_advertiser(ctx, advertiser_id)
        request = LaunchAuthorizationUpsertRequest(
            allowed_product_ids=allowed_product_ids,
            allowed_aweme_ids=allowed_aweme_ids or [],
            allowed_material_ids=allowed_material_ids or [],
            allowed_marketing_goals=allowed_marketing_goals,
            allowed_marketing_scenes=allowed_marketing_scenes,
            max_initial_campaign_budget=_decimal(max_initial_campaign_budget),
            max_initial_ad_budget=_decimal(max_initial_ad_budget),
            min_account_balance=_decimal(min_account_balance),
            min_product_inventory=min_product_inventory,
            max_campaigns_per_day=max_campaigns_per_day,
            max_ads_per_day=max_ads_per_day,
            allow_campaign_create=allow_campaign_create,
            allow_ad_create=allow_ad_create,
        )
        with _db_session(ctx) as db:
            persistence = PersistenceService(db)
            existed = persistence.get_launch_authorization(advertiser_id) is not None
            authorization = persistence.save_launch_authorization(
                advertiser_id,
                request,
                operator_id=_operator_id(ctx),
                user_requested_update=user_requested_update,
                user_request_summary=user_request_summary,
            )
            return {
                "status": "updated" if existed else "configured",
                "locked": True,
                "instruction_to_agent": (
                    "Reuse this authorization. Discovery and preview are read-only; actual "
                    "creation remains blocked until all independent write gates pass."
                ),
                "authorization": _model_dump(
                    serialize_launch_authorization(authorization)
                ),
            }
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_preview_launch_plan(
    ctx: McpContext,
    campaign_payload: dict[str, Any] | None = None,
    ad_payload: dict[str, Any] | None = None,
    advertiser_id: int = 1811432276115675,
    launch_type: str = "STANDARD",
) -> dict[str, Any]:
    """Preview STANDARD or UNI_AWEME launch boundaries without writing or altering payloads."""
    try:
        _ensure_autonomous_advertiser(ctx, advertiser_id)
        for payload in (campaign_payload, ad_payload):
            if payload is not None and payload_advertiser_id(payload) != advertiser_id:
                raise ValidationError("Preview payload advertiser_id does not match target account.")
        service = _strategy_service(ctx)
        product_ids = payload_product_ids(ad_payload or {})
        inventory: dict[str, int] = {}
        product_channels: dict[str, dict[str, Any]] = {}
        product_card_image_ids: dict[str, set[str]] = {}
        uni_aweme_account: dict[str, Any] | None = None
        if (ad_payload or {}).get("marketing_goal") == "LIVE_PROM_GOODS" and normalize_launch_type(launch_type) == UNI_AWEME_LAUNCH_TYPE:
            aweme_id = extract_required_aweme_id(ad_payload)
            eligibility = service.get_uni_authorized_aweme_accounts({"advertiser_id": advertiser_id, "filtering": {"marketing_goal": "LIVE_PROM_GOODS", "scene": "CREATE"}, "page": 1, "page_size": 100})
            uni_aweme_account = extract_uni_aweme_account(eligibility, aweme_id)
        if product_ids:
            normalized_launch_type = normalize_launch_type(launch_type)
            if normalized_launch_type == UNI_AWEME_LAUNCH_TYPE:
                aweme_id = extract_required_aweme_id(ad_payload or {})
                product_result = service.get_uni_promotion_products(
                    {
                        "advertiser_id": advertiser_id,
                        "aweme_id": aweme_id,
                        "filtering": {
                            "product_ids": [int(item) for item in sorted(product_ids)]
                        },
                        "page": 1,
                        "page_size": min(100, len(product_ids)),
                    }
                )
                eligibility_result = service.get_uni_authorized_aweme_accounts(
                    {
                        "advertiser_id": advertiser_id,
                        "filtering": {
                            "marketing_goal": str(
                                (ad_payload or {}).get("marketing_goal") or ""
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
            else:
                product_result = service.get_available_products(
                    {
                        "advertiser_id": advertiser_id,
                        "filter": {"product_ids": [int(item) for item in sorted(product_ids)]},
                        "page": 1,
                        "page_size": min(100, len(product_ids)),
                    }
                )
            inventory = extract_product_inventory(product_result)
            product_channels = extract_product_channels(product_result)
            product_card_image_ids = extract_product_card_image_ids(product_result)
        balance_result = service.get_account_balance({"advertiser_id": advertiser_id})
        balance = extract_account_valid_balance(balance_result)
        with _db_session(ctx) as db:
            authorization = PersistenceService(db).get_launch_authorization(advertiser_id)
            result = launch_preview(
                authorization=authorization,
                campaign_payload=campaign_payload,
                ad_payload=ad_payload,
                product_inventory=inventory,
                product_channels=product_channels,
                product_card_image_ids=product_card_image_ids,
                uni_aweme_account=uni_aweme_account,
                account_valid_balance=balance,
                launch_type=launch_type,
            )
        result["preflight"] = {
            "product_inventory": inventory,
            "product_channels": product_channels,
            "product_card_image_ids": {
                key: sorted(value) for key, value in product_card_image_ids.items()
            },
            "uni_aweme_eligibility": uni_aweme_account,
            "account_valid_balance": str(balance) if balance is not None else None,
        }
        return result
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_save_autonomy_profile(
    ctx: McpContext,
    target_roi: float,
    gross_margin_rate: float,
    refund_rate: float,
    extra_cost_rate: float,
    max_budget: float,
    max_daily_increase: float,
    advertiser_id: int = 1811432276115675,
    user_requested_update: bool = False,
    user_request_summary: str | None = None,
) -> dict[str, Any]:
    """Persist explicit user answers once; existing values stay locked unless the user asks to change them."""
    try:
        _ensure_autonomous_advertiser(ctx, advertiser_id)
        request = AutonomyProfileUpsertRequest(
            target_roi=_decimal(target_roi),
            gross_margin_rate=_decimal(gross_margin_rate),
            refund_rate=_decimal(refund_rate),
            extra_cost_rate=_decimal(extra_cost_rate),
            max_budget=_decimal(max_budget),
            max_daily_increase=_decimal(max_daily_increase),
        )
        if request.max_budget <= _settings(ctx).qianchuan_autonomous_min_budget:
            raise ValidationError(
                "max_budget must be greater than the configured autonomous minimum budget."
            )
        with _db_session(ctx) as db:
            persistence = PersistenceService(db)
            existed = persistence.get_autonomy_profile(advertiser_id) is not None
            profile = persistence.save_autonomy_profile(
                advertiser_id,
                request,
                operator_id=_operator_id(ctx),
                user_requested_update=user_requested_update,
                user_request_summary=user_request_summary,
            )
            return {
                "status": "updated" if existed else "configured",
                "locked": True,
                "instruction_to_agent": (
                    "Remember and reuse this profile. Do not ask again unless the user explicitly "
                    "requests a modification."
                ),
                "profile": _model_dump(serialize_autonomy_profile(profile)),
            }
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_run_autonomous_budget(ctx: McpContext) -> dict[str, Any]:
    """Run one guarded budget cycle; missing required inputs returns setup_required before any API call."""
    try:
        with _db_session(ctx) as db:
            profile = PersistenceService(db).get_autonomy_profile(
                int(_settings(ctx).qianchuan_autonomous_advertiser_id or "0")
            )
            result = AutonomousBudgetController(
                settings=_settings(ctx),
                db=db,
                strategy_service=_strategy_service(ctx) if profile is not None else None,
            ).run_once()
            return asdict(result)
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_update_budget(
    ctx: McpContext,
    advertiser_id: int,
    data: list[dict[str, Any]],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update Qianchuan ad budgets after explicit confirmation."""
    try:
        payload = BudgetUpdateRequest(
            advertiser_id=advertiser_id,
            data=data,
            confirm=confirm,
            reason=reason,
        )
        return _write_action(ctx, "ad_budget.update", payload, "apply_budget_updates")
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_update_bid(
    ctx: McpContext,
    advertiser_id: int,
    data: list[dict[str, Any]],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update Qianchuan ad bids after explicit confirmation."""
    try:
        payload = BidUpdateRequest(
            advertiser_id=advertiser_id,
            data=data,
            confirm=confirm,
            reason=reason,
        )
        return _write_action(ctx, "ad_bid.update", payload, "apply_bid_updates")
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_update_roi_goal(
    ctx: McpContext,
    advertiser_id: int,
    data: list[dict[str, Any]],
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update Qianchuan ROI goals after explicit confirmation."""
    try:
        payload = RoiGoalUpdateRequest(
            advertiser_id=advertiser_id,
            data=data,
            confirm=confirm,
            reason=reason,
        )
        return _write_action(ctx, "roi_goal.update", payload, "apply_roi_goal_updates")
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_update_ad_status(
    ctx: McpContext,
    advertiser_id: int,
    ad_ids: list[int],
    operation: str,
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Enable, disable, or delete Qianchuan ads after explicit confirmation."""
    try:
        payload = AdStatusUpdateRequest(
            advertiser_id=advertiser_id,
            ad_ids=ad_ids,
            operation=operation,
            confirm=confirm,
            reason=reason,
        )
        return _write_action(ctx, "ad_status.update", payload, "update_ad_status")
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


@mcp.tool()
def qianchuan_call_raw_tool(
    ctx: McpContext,
    tool_key: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    confirm: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """Call a supported raw Qianchuan API tool by key."""
    if tool_key == "qianchuan_uni_aweme_ad_create_v1" and (payload or params or {}).get("marketing_goal") == "LIVE_PROM_GOODS":
        raise ToolError("Live UNI creation must use qianchuan_create_live_uni_ad; raw creation cannot bypass the live gate.")
    try:
        request = ToolCallRequest(
            params=params or {},
            payload=payload or {},
            confirm=confirm,
            reason=reason,
        )
        with _db_session(ctx) as db:
            persistence = PersistenceService(db)
            tool_spec = get_tool(tool_key)
            if tool_spec is not None and tool_spec.write:
                persistence.ensure_account_policy_allows(
                    f"tool.{tool_key}",
                    request.payload or request.params,
                    require_policy=_settings(ctx).qianchuan_require_account_policy_for_writes,
                )
                persistence.ensure_action_cooldown(
                    f"tool.{tool_key}",
                    request.payload or request.params,
                    _settings(ctx).qianchuan_write_cooldown_minutes,
                )
            response = _strategy_service(ctx).call_tool(tool_key, request)
            persistence.write_audit(
                operator_id=_operator_id(ctx),
                action_code=f"tool.{tool_key}",
                target_type="qianchuan_tool",
                target_id=tool_key,
                request_payload=request.model_dump(mode="json"),
                response_payload=response,
            )
        return response
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


def main() -> None:
    mcp.run()


def _settings(ctx: McpContext) -> Settings:
    return ctx.request_context.lifespan_context.settings


def _operator_id(ctx: McpContext) -> str:
    return _settings(ctx).mcp_operator_id[:64] or "mcp-agent"


@contextmanager
def _db_session(ctx: McpContext) -> Iterator[Session]:
    db = ctx.request_context.lifespan_context.session_factory()
    try:
        yield db
    finally:
        db.close()


def _strategy_service(ctx: McpContext) -> QianchuanStrategyService:
    settings = _settings(ctx)
    try:
        access_token = resolve_access_token(settings)
    except TokenResolutionError as exc:
        raise _tool_error(exc) from exc
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


def _read_call(ctx: McpContext, func) -> dict[str, Any]:
    try:
        return func(_strategy_service(ctx))
    except _EXPECTED_ERRORS as exc:
        raise _tool_error(exc) from exc


def _ensure_mcp_admin_tools_enabled(ctx: McpContext) -> None:
    if not _settings(ctx).qianchuan_mcp_admin_tools_enabled:
        raise ToolError(
            "MCP admin tools are disabled. Change account policy through the local admin API, "
            "then restart the MCP service."
        )


def _ensure_autonomous_advertiser(ctx: McpContext, advertiser_id: int) -> None:
    configured = str(_settings(ctx).qianchuan_autonomous_advertiser_id or "")
    if str(advertiser_id) != configured:
        raise ValidationError(
            f"Autonomy profile is fixed to advertiser {configured}; received {advertiser_id}."
        )


def _write_cooldown_minutes(ctx: McpContext, action_code: str) -> int:
    if action_code in {
        "roi_goal.update",
        "tool.qianchuan_roi_goal_update_v1",
        "tool.qianchuan_uni_promotion_ad_roi2_goal_update_v1",
    }:
        return _settings(ctx).qianchuan_roi_write_cooldown_minutes
    if action_code in {
        "ad_status.update",
        "tool.qianchuan_ad_status_update_v1",
        "tool.qianchuan_uni_promotion_ad_status_update_v1",
    }:
        return _settings(ctx).qianchuan_status_write_cooldown_minutes
    return _settings(ctx).qianchuan_write_cooldown_minutes


def _normalize_status_write_payload(
    payload: dict[str, Any] | None,
    params: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the canonical body for the guarded status tool.

    ``params`` was accidentally used as a write body by an earlier unattended
    client.  Supporting that exact legacy shape avoids a silent no-op, while
    rejecting extra or conflicting fields prevents a query object from being
    smuggled into a write request.
    """
    canonical = dict(payload or {})
    legacy = dict(params or {})
    if canonical:
        if legacy:
            supported = {"advertiser_id", "ad_ids", "opt_status"}
            unsupported = set(legacy) - supported
            if unsupported:
                raise ValidationError(
                    "Status params compatibility accepts only advertiser_id, ad_ids, and opt_status."
                )
            for key, value in legacy.items():
                if key in canonical and canonical[key] != value:
                    raise ValidationError(
                        f"Conflicting status field {key!r} was supplied in payload and params."
                    )
        return canonical

    if not legacy:
        return canonical
    supported = {"advertiser_id", "ad_ids", "opt_status"}
    unsupported = set(legacy) - supported
    if unsupported:
        raise ValidationError(
            "Status params compatibility accepts only advertiser_id, ad_ids, and opt_status."
        )
    missing = supported - set(legacy)
    if missing:
        raise ValidationError(
            "Legacy status params must include advertiser_id, ad_ids, and opt_status."
        )
    return {key: legacy[key] for key in ("advertiser_id", "ad_ids", "opt_status")}


def _write_action(
    ctx: McpContext,
    action_code: str,
    payload,
    method_name: str,
) -> dict[str, Any]:
    with _db_session(ctx) as db:
        persistence = PersistenceService(db)
        try:
            persistence.ensure_account_policy_allows(
                action_code,
                payload,
                require_policy=_settings(ctx).qianchuan_require_account_policy_for_writes,
            )
            persistence.ensure_action_cooldown(
                action_code,
                payload,
                _write_cooldown_minutes(ctx, action_code),
            )
            settings = _settings(ctx)
            if action_code == "ad_budget.update" and settings.qianchuan_autonomous_enabled:
                profile = persistence.get_autonomy_profile(payload.advertiser_id)
                if profile is None:
                    raise ValidationError(
                        "Autonomy profile is missing. Ask the user for all required financial "
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
            response = getattr(_strategy_service(ctx), method_name)(payload)
        except _EXPECTED_ERRORS as exc:
            persistence.write_audit(
                operator_id=_operator_id(ctx),
                action_code=action_code,
                target_type="qianchuan_ad",
                target_id=str(payload.advertiser_id),
                request_payload=payload.model_dump(mode="json"),
                response_payload={},
                error_message=str(exc),
            )
            raise
        persistence.write_audit(
            operator_id=_operator_id(ctx),
            action_code=action_code,
            target_type="qianchuan_ad",
            target_id=str(payload.advertiser_id),
            request_payload=payload.model_dump(mode="json"),
            response_payload=response,
        )
        return response


def _write_tool_action(
    ctx: McpContext,
    action_code: str,
    request: ToolCallRequest,
    method_name: str,
) -> dict[str, Any]:
    request_payload = request.payload
    target_id = _target_id_from_payload(request_payload)
    with _db_session(ctx) as db:
        persistence = PersistenceService(db)
        try:
            if not request_payload:
                raise ValidationError("Write tool payload must include advertiser_id.")
            if action_code == "tool.qianchuan_uni_aweme_ad_create_v1" and request_payload.get("marketing_goal") == "LIVE_PROM_GOODS" and not _settings(ctx).qianchuan_live_create_enabled:
                raise WriteDisabledError("Live UNI creation is disabled; QIANCHUAN_LIVE_CREATE_ENABLED must be explicitly configured.")
            persistence.ensure_launch_authorization_allows(action_code, request_payload)
            persistence.ensure_account_policy_allows(
                action_code,
                request_payload,
                require_policy=_settings(ctx).qianchuan_require_account_policy_for_writes,
            )
            persistence.ensure_action_cooldown(
                action_code,
                request_payload,
                _write_cooldown_minutes(ctx, action_code),
            )
            service = _strategy_service(ctx)
            if (
                action_code
                in {
                    "tool.qianchuan_campaign_create_v1",
                    "tool.qianchuan_ad_create_v1",
                    "tool.qianchuan_uni_aweme_ad_create_v1",
                }
                and _settings(ctx).qianchuan_write_enabled
                and request.confirm
            ):
                advertiser_id = int(request_payload["advertiser_id"])
                product_ids = payload_product_ids(request_payload)
                inventory: dict[str, int] | None = None
                product_channels: dict[str, dict[str, Any]] | None = None
                product_card_image_ids: dict[str, set[str]] | None = None
                uni_aweme_account: dict[str, Any] | None = None
                if action_code == "tool.qianchuan_uni_aweme_ad_create_v1" and request_payload.get("marketing_goal") == "LIVE_PROM_GOODS":
                    aweme_id = extract_required_aweme_id(request_payload)
                    eligibility = service.get_uni_authorized_aweme_accounts({"advertiser_id": advertiser_id, "filtering": {"marketing_goal": "LIVE_PROM_GOODS", "scene": "CREATE"}, "page": 1, "page_size": 100})
                    uni_aweme_account = extract_uni_aweme_account(eligibility, aweme_id)
                    enforce_uni_aweme_create_eligibility(uni_aweme_account, aweme_id=aweme_id, marketing_goal="LIVE_PROM_GOODS")
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
            response = getattr(service, method_name)(request)
        except _EXPECTED_ERRORS as exc:
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
                operator_id=_operator_id(ctx),
                action_code=action_code,
                target_type="qianchuan_launch",
                target_id=target_id,
                request_payload=request.model_dump(mode="json"),
                response_payload=error_response,
                error_message=str(exc),
            )
            raise _tool_error(exc) from exc
        persistence.write_audit(
            operator_id=_operator_id(ctx),
            action_code=action_code,
            target_type="qianchuan_launch",
            target_id=target_id,
            request_payload=request.model_dump(mode="json"),
            response_payload=response,
        )
        return response


def _upload_video_action(
    ctx: McpContext,
    *,
    advertiser_id: int,
    prepared,
    is_aigc: bool,
    labels: list[str],
    confirm: bool,
    reason: str | None,
) -> dict[str, Any]:
    action_code = "tool.qianchuan_file_video_ad_v2"
    audit_payload = {
        "advertiser_id": advertiser_id,
        "filename": prepared.filename,
        "size_bytes": prepared.size_bytes,
        "video_signature": prepared.video_signature,
        "is_aigc": is_aigc,
        "labels": labels,
        "confirm": confirm,
        "reason": reason,
    }
    with _db_session(ctx) as db:
        persistence = PersistenceService(db)
        try:
            persistence.ensure_account_policy_allows(
                action_code,
                audit_payload,
                require_policy=_settings(ctx).qianchuan_require_account_policy_for_writes,
            )
            persistence.ensure_action_cooldown(
                action_code,
                audit_payload,
                _settings(ctx).qianchuan_write_cooldown_minutes,
            )
            service = _strategy_service(ctx)
            try:
                upload_response = service.upload_video(
                    advertiser_id=advertiser_id,
                    file_path=prepared.path,
                    filename=prepared.filename,
                    video_signature=prepared.video_signature,
                    content_type=prepared.content_type,
                    is_aigc=is_aigc,
                    labels=labels,
                    confirm=confirm,
                    reason=reason,
                )
            except QianchuanApiError as exc:
                if not _is_ambiguous_transport_error(exc):
                    raise
                reconciliation = _reconcile_video_upload(
                    service,
                    advertiser_id=advertiser_id,
                    video_signature=prepared.video_signature,
                )
                if _video_rows(reconciliation):
                    response = _video_upload_result(
                        advertiser_id=advertiser_id,
                        prepared=prepared,
                        upload_response=None,
                        verification=reconciliation,
                        reconciled_after_unknown_result=True,
                    )
                    persistence.write_audit(
                        operator_id=_operator_id(ctx),
                        action_code=action_code,
                        target_type="qianchuan_video",
                        target_id=str(advertiser_id),
                        request_payload=audit_payload,
                        response_payload=response,
                    )
                    return response
                unknown_response = {
                    "status": "upload_result_unknown",
                    "video_signature": prepared.video_signature,
                    "instruction": "Do not retry; read back by signature before any later upload.",
                }
                # Treat an ambiguous submission as a cooldown conflict so an agent cannot
                # immediately send the same bytes again.
                persistence.write_audit(
                    operator_id=_operator_id(ctx),
                    action_code=action_code,
                    target_type="qianchuan_video",
                    target_id=str(advertiser_id),
                    request_payload=audit_payload,
                    response_payload=unknown_response,
                )
                return unknown_response

            data = upload_response.get("data") if isinstance(upload_response, dict) else None
            video_id = data.get("video_id") if isinstance(data, dict) else None
            filtering = (
                {"video_ids": [str(video_id)]}
                if video_id not in (None, "")
                else {"signatures": [prepared.video_signature]}
            )
            try:
                verification = service.get_videos(
                    {
                        "advertiser_id": advertiser_id,
                        "filtering": filtering,
                        "page": 1,
                        "page_size": 10,
                    }
                )
            except QianchuanApiError as exc:
                verification = {
                    "status": "verification_pending",
                    "message": str(exc),
                    "code": exc.code,
                    "request_id": exc.request_id,
                }
            response = _video_upload_result(
                advertiser_id=advertiser_id,
                prepared=prepared,
                upload_response=upload_response,
                verification=verification,
                reconciled_after_unknown_result=False,
            )
        except _EXPECTED_ERRORS as exc:
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
                operator_id=_operator_id(ctx),
                action_code=action_code,
                target_type="qianchuan_video",
                target_id=str(advertiser_id),
                request_payload=audit_payload,
                response_payload=error_response,
                error_message=str(exc),
            )
            raise _tool_error(exc) from exc
        persistence.write_audit(
            operator_id=_operator_id(ctx),
            action_code=action_code,
            target_type="qianchuan_video",
            target_id=str(advertiser_id),
            request_payload=audit_payload,
            response_payload=response,
        )
        return response


def _upload_image_action(
    ctx: McpContext,
    *,
    advertiser_id: int,
    prepared,
    is_aigc: bool,
    confirm: bool,
    reason: str | None,
) -> dict[str, Any]:
    action_code = "tool.qianchuan_file_image_ad_v2"
    audit_payload = {
        "advertiser_id": advertiser_id,
        "filename": prepared.filename,
        "size_bytes": prepared.size_bytes,
        "image_signature": prepared.image_signature,
        "width": prepared.width,
        "height": prepared.height,
        "is_aigc": is_aigc,
        "confirm": confirm,
        "reason": reason,
    }
    with _db_session(ctx) as db:
        persistence = PersistenceService(db)
        try:
            persistence.ensure_account_policy_allows(
                action_code,
                audit_payload,
                require_policy=_settings(ctx).qianchuan_require_account_policy_for_writes,
            )
            persistence.ensure_action_cooldown(
                action_code,
                audit_payload,
                _settings(ctx).qianchuan_write_cooldown_minutes,
            )
            upload_response = _strategy_service(ctx).upload_image(
                advertiser_id=advertiser_id,
                file_path=prepared.path,
                filename=prepared.filename,
                image_signature=prepared.image_signature,
                content_type=prepared.content_type,
                is_aigc=is_aigc,
                confirm=confirm,
                reason=reason,
            )
            data = upload_response.get("data") if isinstance(upload_response, dict) else None
            image_id = data.get("id") if isinstance(data, dict) else None
            response = {
                "status": "uploaded" if image_id not in (None, "") else "verification_pending",
                "advertiser_id": advertiser_id,
                "image_id": image_id,
                "material_id": data.get("material_id") if isinstance(data, dict) else None,
                "image_signature": prepared.image_signature,
                "filename": prepared.filename,
                "size_bytes": prepared.size_bytes,
                "width": prepared.width,
                "height": prepared.height,
                "request_id": upload_response.get("request_id") if isinstance(upload_response, dict) else None,
                "official_response": upload_response,
                "instruction": "Image is in the material library only; it is not bound to any plan.",
            }
        except _EXPECTED_ERRORS as exc:
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
                operator_id=_operator_id(ctx),
                action_code=action_code,
                target_type="qianchuan_image",
                target_id=str(advertiser_id),
                request_payload=audit_payload,
                response_payload=error_response,
                error_message=str(exc),
            )
            raise _tool_error(exc) from exc
        persistence.write_audit(
            operator_id=_operator_id(ctx),
            action_code=action_code,
            target_type="qianchuan_image",
            target_id=str(advertiser_id),
            request_payload=audit_payload,
            response_payload=response,
        )
        return response


def _is_ambiguous_transport_error(exc: QianchuanApiError) -> bool:
    return exc.code is None and exc.request_id is None and exc.status_code is None


def _reconcile_video_upload(
    service: QianchuanStrategyService,
    *,
    advertiser_id: int,
    video_signature: str,
) -> dict[str, Any]:
    try:
        return service.get_videos(
            {
                "advertiser_id": advertiser_id,
                "filtering": {"signatures": [video_signature]},
                "page": 1,
                "page_size": 10,
            }
        )
    except QianchuanApiError as exc:
        return {
            "status": "reconciliation_failed",
            "message": str(exc),
            "code": exc.code,
            "request_id": exc.request_id,
        }


def _video_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data") if isinstance(response, dict) else None
    rows = data.get("list") if isinstance(data, dict) else None
    return [item for item in (rows or []) if isinstance(item, dict)]


def _video_upload_result(
    *,
    advertiser_id: int,
    prepared,
    upload_response: dict[str, Any] | None,
    verification: dict[str, Any],
    reconciled_after_unknown_result: bool,
) -> dict[str, Any]:
    upload_data = upload_response.get("data") if isinstance(upload_response, dict) else None
    verified_rows = _video_rows(verification)
    verified_video_id = verified_rows[0].get("video_id") if verified_rows else None
    video_id = upload_data.get("video_id") if isinstance(upload_data, dict) else None
    return {
        "status": "uploaded",
        "advertiser_id": advertiser_id,
        "filename": prepared.filename,
        "size_bytes": prepared.size_bytes,
        "video_signature": prepared.video_signature,
        "video_id": video_id or verified_video_id,
        "material_id": upload_data.get("material_id") if isinstance(upload_data, dict) else None,
        "official_upload": upload_response,
        "verification": verification,
        "verified": bool(verified_rows),
        "reconciled_after_unknown_result": reconciled_after_unknown_result,
        "bound_to_product_or_plan": False,
        "plan_status_changed": False,
        "next_step": (
            "Review the returned material, then explicitly add its video_id to allowed_material_ids "
            "before any separate material-bind request."
        ),
    }


def _target_id_from_payload(payload) -> str:
    advertiser_id = payload_advertiser_id(payload)
    return str(advertiser_id) if advertiser_id is not None else "unknown"


def _report_query(
    *,
    advertiser_id: int,
    start_date: str,
    end_date: str,
    marketing_goal: str,
    order_platform: str,
    fields: list[str] | None = None,
    filtering: dict[str, Any] | None = None,
    ad_ids: list[int] | None = None,
    group_by: list[str] | None = None,
    time_granularity: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> ReportQuery:
    payload: dict[str, Any] = {
        "advertiser_id": advertiser_id,
        "start_date": start_date,
        "end_date": end_date,
        "marketing_goal": marketing_goal,
        "order_platform": order_platform,
        "filtering": filtering or {},
        "ad_ids": ad_ids or [],
        "group_by": group_by or [],
        "time_granularity": time_granularity,
        "page": page,
        "page_size": page_size,
    }
    if fields is not None:
        payload["fields"] = fields
    try:
        return ReportQuery(**payload)
    except PydanticValidationError as exc:
        raise _tool_error(exc) from exc


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be YYYY-MM-DD.") from exc


def _optional_date(value: str | None) -> date | None:
    return _parse_date(value, "date") if value else None


def _optional_decimal(value: float | int | str | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _decimal(value: float | int | str) -> Decimal:
    return Decimal(str(value))


def _model_dump(value) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _tool_error(exc: Exception) -> ToolError:
    if isinstance(exc, QianchuanApiError):
        message = str(exc)
        if exc.code is not None:
            message = f"{message} code={exc.code}"
        if exc.status_code is not None:
            message = f"{message} http_status={exc.status_code}"
        if exc.request_id:
            message = f"{message} request_id={exc.request_id}"
        return ToolError(message)
    if isinstance(exc, DecisionNotFoundError):
        return ToolError(f"Decision {exc} was not found.")
    return ToolError(str(exc))


_EXPECTED_ERRORS = (
    QianchuanApiError,
    TokenResolutionError,
    StrategyError,
    WriteDisabledError,
    ConfirmationRequiredError,
    ValidationError,
    PydanticValidationError,
    DecisionNotFoundError,
)


if __name__ == "__main__":
    main()
