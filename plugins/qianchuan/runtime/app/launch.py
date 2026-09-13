from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import unquote, urlparse

from app.carousel_binding import CarouselBindingValidationError, normalize_carousel_material
from app.orchestration import payload_aweme_ids, payload_material_ids, payload_product_ids
from app.strategy import ValidationError


CREATE_CAMPAIGN_ACTION = "tool.qianchuan_campaign_create_v1"
CREATE_STANDARD_AD_ACTION = "tool.qianchuan_ad_create_v1"
CREATE_UNI_AWEME_AD_ACTION = "tool.qianchuan_uni_aweme_ad_create_v1"
CREATE_AD_ACTIONS = {
    CREATE_STANDARD_AD_ACTION,
    CREATE_UNI_AWEME_AD_ACTION,
}

STANDARD_LAUNCH_TYPE = "STANDARD"
# This is an internal authorization sentinel, not an Ocean Engine request field.
UNI_AWEME_LAUNCH_TYPE = "UNI_AWEME"
SUPPORTED_LAUNCH_TYPES = {STANDARD_LAUNCH_TYPE, UNI_AWEME_LAUNCH_TYPE}


def launch_authorization_questions() -> list[dict[str, str]]:
    return [
        {
            "field": "allowed_product_ids",
            "question": "允许 Agent 自主投放的商品 ID 有哪些？必须从可投商品接口返回值中选择。",
        },
        {
            "field": "allowed_aweme_ids",
            "question": "允许使用哪些已授权抖音号 ID？没有抖音号场景可传空列表。",
        },
        {
            "field": "allowed_material_ids",
            "question": "允许使用哪些素材 ID？使用千川自动生成素材时可传空列表。",
        },
        {
            "field": "allowed_marketing_goals / allowed_marketing_scenes",
            "question": (
                "允许的营销目标和营销场景枚举值是什么？"
                "全域抖音号投放使用内部授权模式 UNI_AWEME（不会写入官方请求）。"
            ),
        },
        {
            "field": "max_initial_campaign_budget / max_initial_ad_budget",
            "question": "单个新 Campaign 和广告的初始预算上限分别是多少元？",
        },
        {
            "field": "min_account_balance / min_product_inventory",
            "question": "创建前要求的最低账户可用余额和最低商品库存是多少？",
        },
        {
            "field": "max_campaigns_per_day / max_ads_per_day",
            "question": "Agent 每天最多允许新建多少个 Campaign 和广告？",
        },
        {
            "field": "allow_campaign_create / allow_ad_create",
            "question": "是否授权 Agent 自主创建 Campaign 和广告？",
        },
    ]


def enforce_launch_authorization(
    *,
    authorization,
    action_code: str,
    payload: dict[str, Any],
    successful_actions_today: int = 0,
    product_inventory: dict[str, int] | None = None,
    product_channels: dict[str, dict[str, Any]] | None = None,
    product_card_image_ids: dict[str, set[str]] | None = None,
    account_valid_balance: Decimal | None = None,
    preflight_complete: bool = False,
) -> None:
    advertiser_id = str(payload.get("advertiser_id", ""))
    if authorization is None:
        raise ValidationError(
            f"Advertiser {advertiser_id} has no locked launch authorization. "
            "Configure it before Agent-created launches."
        )
    if not authorization.locked:
        raise ValidationError("Launch authorization must be locked before use.")
    if str(authorization.advertiser_id) != advertiser_id:
        raise ValidationError("Launch authorization advertiser_id does not match payload.")

    if action_code == CREATE_CAMPAIGN_ACTION:
        if not authorization.allow_campaign_create:
            raise ValidationError("Launch authorization does not allow campaign creation.")
        _enforce_daily_limit(successful_actions_today, authorization.max_campaigns_per_day, "campaign")
        _enforce_budget(payload.get("budget"), authorization.max_initial_campaign_budget, "campaign")
    elif action_code in CREATE_AD_ACTIONS:
        if action_code == CREATE_UNI_AWEME_AD_ACTION:
            _validate_uni_aweme_create_payload(payload)
        if not authorization.allow_ad_create:
            raise ValidationError("Launch authorization does not allow ad creation.")
        _enforce_daily_limit(successful_actions_today, authorization.max_ads_per_day, "ad")
        delivery_setting = payload.get("delivery_setting")
        budget = delivery_setting.get("budget") if isinstance(delivery_setting, dict) else None
        _enforce_budget(budget, authorization.max_initial_ad_budget, "ad")
        _enforce_id_scope(
            payload_product_ids(payload),
            set(authorization.allowed_product_ids or []),
            "product",
            required=True,
        )
        _enforce_id_scope(
            payload_aweme_ids(payload),
            set(authorization.allowed_aweme_ids or []),
            "aweme",
            required=False,
        )
        material_ids = payload_material_ids(payload)
        if material_ids:
            _enforce_id_scope(
                material_ids,
                set(authorization.allowed_material_ids or []),
                "material",
                required=True,
            )
        if preflight_complete and product_inventory is None:
            raise ValidationError("Official product preflight result is missing.")
        if product_inventory is not None:
            _enforce_inventory(
                payload_product_ids(payload),
                product_inventory,
                authorization.min_product_inventory,
            )
        if action_code == CREATE_UNI_AWEME_AD_ACTION and product_channels is not None:
            _enforce_uni_product_channels(payload, product_channels)
        if action_code == CREATE_UNI_AWEME_AD_ACTION and product_card_image_ids is not None:
            _enforce_uni_product_card_images(payload, product_card_image_ids)
    else:
        return

    _enforce_enum_scope(
        payload.get("marketing_goal"),
        authorization.allowed_marketing_goals,
        "marketing_goal",
    )
    marketing_scene = _authorization_marketing_scene(action_code, payload)
    _enforce_enum_scope(
        marketing_scene,
        authorization.allowed_marketing_scenes,
        "marketing_scene/campaign_scene",
    )
    minimum = Decimal(str(authorization.min_account_balance))
    if preflight_complete and minimum > 0 and account_valid_balance is None:
        raise ValidationError("Official account_valid balance is missing from launch preflight.")
    if account_valid_balance is not None:
        if account_valid_balance < minimum:
            raise ValidationError(
                f"Account valid balance {account_valid_balance} is below launch minimum {minimum}."
            )


def extract_product_inventory(payload: dict[str, Any]) -> dict[str, int]:
    data = payload.get("data")
    candidates = [data, payload] if isinstance(data, dict) else [payload]
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("product_list", "list", "items", "rows"):
            value = candidate.get(key)
            if isinstance(value, list):
                rows = [row for row in value if isinstance(row, dict)]
                break
        if rows:
            break
    result: dict[str, int] = {}
    for row in rows:
        product_id = row.get("id") if row.get("id") is not None else row.get("product_id")
        if product_id is None:
            continue
        try:
            # Standard available-product responses expose ``inventory`` while
            # PC uni-promotion responses expose the same concept as ``stock_num``.
            stock = row.get("inventory")
            if stock is None:
                stock = row.get("stock_num")
            result[str(product_id)] = int(stock or 0)
        except (TypeError, ValueError):
            result[str(product_id)] = 0
    return result


def extract_product_channels(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    data = payload.get("data")
    candidates = [data, payload] if isinstance(data, dict) else [payload]
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("product_list", "list", "items", "rows"):
            value = candidate.get(key)
            if isinstance(value, list):
                rows = [row for row in value if isinstance(row, dict)]
                break
        if rows:
            break
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        product_id = row.get("id") if row.get("id") is not None else row.get("product_id")
        if product_id is None:
            continue
        result[str(product_id)] = {
            "channel_id": row.get("channel_id"),
            "channel_type": row.get("channel_type"),
        }
    return result


def extract_product_card_image_ids(payload: dict[str, Any]) -> dict[str, set[str]]:
    """Extract official Qianchuan image IDs from uni-product image URLs by product."""
    data = payload.get("data")
    candidates = [data, payload] if isinstance(data, dict) else [payload]
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("product_list", "list", "items", "rows"):
            value = candidate.get(key)
            if isinstance(value, list):
                rows = [row for row in value if isinstance(row, dict)]
                break
        if rows:
            break

    result: dict[str, set[str]] = {}
    for row in rows:
        product_id = row.get("id") if row.get("id") is not None else row.get("product_id")
        if product_id is None:
            continue
        image_values: list[Any] = [row.get("img")]
        for item in row.get("square_image_list") or []:
            if isinstance(item, dict):
                image_values.extend((item.get("id"), item.get("image_id"), item.get("img_url")))
            else:
                image_values.append(item)
        image_ids = {
            image_id
            for value in image_values
            if (image_id := _qianchuan_image_id(value)) is not None
        }
        result[str(product_id)] = image_ids
    return result


def extract_account_valid_balance(payload: dict[str, Any]) -> Decimal | None:
    data = payload.get("data")
    source = data if isinstance(data, dict) else payload
    value = source.get("account_valid") if isinstance(source, dict) else None
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def extract_required_aweme_id(payload: dict[str, Any]) -> int:
    """Return the single Aweme identity required by PC uni-promotion product checks."""
    aweme_ids = payload_aweme_ids(payload)
    if len(aweme_ids) != 1:
        raise ValidationError(
            "UNI_AWEME payload must include exactly one aweme_id for official product preflight."
        )
    try:
        return int(next(iter(aweme_ids)))
    except (TypeError, ValueError) as exc:
        raise ValidationError("UNI_AWEME payload aweme_id is invalid.") from exc


def extract_uni_aweme_account(
    payload: dict[str, Any], aweme_id: int | str
) -> dict[str, Any] | None:
    data = payload.get("data")
    source = data if isinstance(data, dict) else payload
    rows = source.get("aweme_id_list") if isinstance(source, dict) else None
    if not isinstance(rows, list):
        return None
    target = str(aweme_id)
    return next(
        (
            row
            for row in rows
            if isinstance(row, dict) and str(row.get("aweme_id")) == target
        ),
        None,
    )


def enforce_uni_aweme_create_eligibility(
    account: dict[str, Any] | None,
    *,
    aweme_id: int | str,
    marketing_goal: str,
) -> None:
    """Validate the official CREATE-scene eligibility record before preview/create."""
    if account is None:
        raise ValidationError(
            f"Aweme {aweme_id} was not returned by the official UNI_AWEME CREATE-scene "
            f"eligibility query for {marketing_goal}."
        )
    if account.get("has_authorized") is not True:
        raise ValidationError(
            f"Aweme {aweme_id} has_authorized is not true in the official CREATE-scene query."
        )
    if account.get("anchor_forbidden") is True:
        raise ValidationError(f"Aweme {aweme_id} is forbidden for UNI_AWEME creation.")
    auth_types = {
        str(item).upper()
        for item in (account.get("auth_type") or [])
        if str(item).strip()
    }
    is_self_authorized = "SELF" in auth_types
    if not is_self_authorized and account.get("can_control_uniprom") is not True:
        apply_type = account.get("product_uni_prom_apply_type")
        raise ValidationError(
            f"Aweme {aweme_id} cannot control UNI_AWEME through the API "
            f"(can_control_uniprom={account.get('can_control_uniprom')}, "
            f"product_uni_prom_apply_type={apply_type}). Query authorizable shops and obtain "
            "product full-domain authorization before preview or creation."
        )
    if marketing_goal == "VIDEO_PROM_GOODS":
        if account.get("has_shop_permission") is not True:
            raise ValidationError(
                f"Aweme {aweme_id} has_shop_permission is not true for product full-domain."
            )
        if account.get("is_product_uni_prom_disabled") is True:
            reasons = account.get("product_disable_reasons") or []
            raise ValidationError(
                f"Aweme {aweme_id} product full-domain is disabled: {reasons}."
            )


def launch_preview(
    *,
    authorization,
    campaign_payload: dict[str, Any] | None,
    ad_payload: dict[str, Any] | None,
    product_inventory: dict[str, int],
    product_channels: dict[str, dict[str, Any]] | None = None,
    product_card_image_ids: dict[str, set[str]] | None = None,
    uni_aweme_account: dict[str, Any] | None = None,
    account_valid_balance: Decimal | None,
    launch_type: str = STANDARD_LAUNCH_TYPE,
) -> dict[str, Any]:
    normalized_launch_type = normalize_launch_type(launch_type)
    if normalized_launch_type == UNI_AWEME_LAUNCH_TYPE:
        if campaign_payload is not None:
            raise ValidationError(
                "UNI_AWEME launch preview does not accept campaign_payload; "
                "the uni-aweme ad is the all-domain promotion plan."
            )
        if ad_payload is None:
            raise ValidationError("UNI_AWEME launch preview requires ad_payload.")
        aweme_id = extract_required_aweme_id(ad_payload)
        enforce_uni_aweme_create_eligibility(
            uni_aweme_account,
            aweme_id=aweme_id,
            marketing_goal=str(ad_payload.get("marketing_goal") or ""),
        )

    checks: list[str] = []
    if campaign_payload:
        enforce_launch_authorization(
            authorization=authorization,
            action_code=CREATE_CAMPAIGN_ACTION,
            payload=campaign_payload,
            product_inventory=product_inventory,
            product_channels=product_channels,
            product_card_image_ids=product_card_image_ids,
            account_valid_balance=account_valid_balance,
            preflight_complete=True,
        )
        checks.append("campaign boundaries passed")
    if ad_payload:
        enforce_launch_authorization(
            authorization=authorization,
            action_code=preview_ad_action(normalized_launch_type),
            payload=ad_payload,
            product_inventory=product_inventory,
            product_channels=product_channels,
            product_card_image_ids=product_card_image_ids,
            account_valid_balance=account_valid_balance,
            preflight_complete=True,
        )
        checks.append(
            "uni-aweme ad/product/inventory boundaries passed"
            if normalized_launch_type == UNI_AWEME_LAUNCH_TYPE
            else "ad/product/inventory boundaries passed"
        )
    if not checks:
        raise ValidationError("Preview requires campaign_payload or ad_payload.")
    return {
        "status": "ready_for_guarded_execution",
        "dry_run": True,
        "launch_type": normalized_launch_type,
        "create_action": preview_ad_action(normalized_launch_type) if ad_payload else CREATE_CAMPAIGN_ACTION,
        "checked_at": datetime.now(UTC).isoformat(),
        "checks": checks,
        "instruction_to_agent": (
            "This preview never writes. Real creation still requires global write_enabled, "
            "account-policy permission, confirm=true, reason, cooldown, and a fresh preflight."
        ),
    }


def normalize_launch_type(value: Any) -> str:
    normalized = str(value or STANDARD_LAUNCH_TYPE).strip().upper()
    if normalized not in SUPPORTED_LAUNCH_TYPES:
        supported = ", ".join(sorted(SUPPORTED_LAUNCH_TYPES))
        raise ValidationError(f"Unsupported launch_type={value}. Expected one of: {supported}.")
    return normalized


def preview_ad_action(launch_type: str) -> str:
    if launch_type == UNI_AWEME_LAUNCH_TYPE:
        return CREATE_UNI_AWEME_AD_ACTION
    return CREATE_STANDARD_AD_ACTION


def _authorization_marketing_scene(action_code: str, payload: dict[str, Any]) -> Any:
    if action_code == CREATE_UNI_AWEME_AD_ACTION:
        # Full-domain creation has no marketing_scene request field. Reuse the existing
        # locked scene allowlist with an internal sentinel, without mutating the payload
        # sent to Ocean Engine.
        return UNI_AWEME_LAUNCH_TYPE
    return payload.get("marketing_scene") or payload.get("campaign_scene")


def _validate_uni_aweme_create_payload(payload: dict[str, Any]) -> None:
    forbidden = [
        field
        for field in ("marketing_scene", "campaign_scene", "launch_type")
        if field in payload
    ]
    if forbidden:
        raise ValidationError(
            "UNI_AWEME official payload must not include local/standard field(s): "
            + ", ".join(forbidden)
            + "."
        )

    delivery_setting = payload.get("delivery_setting")
    if not isinstance(delivery_setting, dict):
        raise ValidationError("UNI_AWEME payload must include delivery_setting.")
    if not delivery_setting.get("smart_bid_type"):
        raise ValidationError(
            "UNI_AWEME delivery_setting must include official smart_bid_type."
        )
    if (
        delivery_setting.get("smart_bid_type") == "SMART_BID_CONSERVATIVE"
        and "roi2_goal" in delivery_setting
    ):
        raise ValidationError(
            "SMART_BID_CONSERVATIVE does not support delivery_setting.roi2_goal."
        )

    required_delivery_fields = (
        "budget",
        "smart_bid_type",
        "qcpx_mode",
        "video_schedule_type",
        "start_time",
        "end_time",
        "daily_delivery_time",
        "deep_external_action",
    )
    missing_delivery_fields = [
        field
        for field in required_delivery_fields
        if delivery_setting.get(field) in (None, "")
    ]
    if missing_delivery_fields:
        raise ValidationError(
            "UNI_AWEME delivery_setting is missing verified create field(s): "
            + ", ".join(missing_delivery_fields)
            + "."
        )
    if delivery_setting.get("qcpx_mode") != "QCPX_MODE_ON":
        raise ValidationError("UNI_AWEME delivery_setting.qcpx_mode must be QCPX_MODE_ON.")
    if delivery_setting.get("video_schedule_type") != "SCHEDULE_FROM_NOW":
        raise ValidationError(
            "VIDEO_PROM_GOODS delivery_setting.video_schedule_type must be "
            "SCHEDULE_FROM_NOW for the guarded create contract."
        )
    if delivery_setting.get("deep_external_action") != "AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI":
        raise ValidationError(
            "UNI_AWEME delivery_setting.deep_external_action must match the verified "
            "AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI contract."
        )
    try:
        daily_delivery_time = Decimal(str(delivery_setting["daily_delivery_time"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(
            "UNI_AWEME delivery_setting.daily_delivery_time must be numeric."
        ) from exc
    if daily_delivery_time < Decimal("0.5"):
        raise ValidationError(
            "UNI_AWEME delivery_setting.daily_delivery_time must be at least 0.5."
        )
    try:
        start_date = date.fromisoformat(str(delivery_setting["start_time"]))
        end_date = date.fromisoformat(str(delivery_setting["end_time"]))
    except ValueError as exc:
        raise ValidationError(
            "UNI_AWEME start_time/end_time must use YYYY-MM-DD dates."
        ) from exc
    if end_date < start_date:
        raise ValidationError("UNI_AWEME end_time cannot be before start_time.")

    if payload.get("marketing_goal") != "VIDEO_PROM_GOODS":
        return
    stale_fields = [
        field
        for field in (
            "creative_setting",
            "product_channel_info",
            "uni_product_info",
            "product_infos",
            "products",
        )
        if field in payload
    ]
    if stale_fields:
        raise ValidationError(
            "Current VIDEO_PROM_GOODS UNI_AWEME create payload must omit stale or "
            "non-documented field(s): " + ", ".join(stale_fields) + "."
        )
    product_ids = payload.get("product_ids")
    if not isinstance(product_ids, list) or not product_ids:
        raise ValidationError(
            "VIDEO_PROM_GOODS UNI_AWEME payload must include non-empty top-level "
            "product_ids as the official delivery product set."
        )
    if any(product_id in (None, "") for product_id in product_ids):
        raise ValidationError("Each UNI_AWEME product_ids item must be non-empty.")

    creative_items = payload.get("multi_product_creative_list")
    if not isinstance(creative_items, list) or not creative_items:
        raise ValidationError(
            "VIDEO_PROM_GOODS UNI_AWEME payload must include non-empty "
            "multi_product_creative_list; the official API rejects product_ids-only "
            "requests as not using the multi-product structure."
        )

    creative_product_ids: set[str] = set()
    allowed_item_fields = {
        "product_id",
        "creative_type",
        "hide_in_aweme",
        "carousel_material",
    }
    for index, item in enumerate(creative_items):
        if not isinstance(item, dict):
            raise ValidationError(
                f"multi_product_creative_list[{index}] must be an object."
            )
        undocumented_fields = sorted(set(item) - allowed_item_fields)
        if undocumented_fields:
            raise ValidationError(
                f"multi_product_creative_list[{index}] must omit undocumented or "
                "response-only field(s): " + ", ".join(undocumented_fields) + "."
            )
        product_id = item.get("product_id")
        if product_id in (None, ""):
            raise ValidationError(
                f"multi_product_creative_list[{index}].product_id is required."
            )
        if item.get("creative_type") != "PROGRAMMATIC_CREATIVE":
            raise ValidationError(
                f"multi_product_creative_list[{index}].creative_type must be "
                "PROGRAMMATIC_CREATIVE for the guarded smart-material contract."
            )
        if item.get("hide_in_aweme") is not True:
            raise ValidationError(
                f"multi_product_creative_list[{index}].hide_in_aweme must be true; "
                "both manually created PC product full-domain plans return this field."
            )
        creative_product_ids.add(str(product_id))
        if "carousel_material" in item:
            carousel_material = item["carousel_material"]
            if not isinstance(carousel_material, list) or not carousel_material:
                raise ValidationError(
                    f"multi_product_creative_list[{index}].carousel_material must be a non-empty list."
                )
            try:
                item["carousel_material"] = [
                    normalize_carousel_material(
                        carousel,
                        prefix=(
                            f"multi_product_creative_list[{index}].carousel_material"
                            f"[{carousel_index}]"
                        ),
                    )
                    for carousel_index, carousel in enumerate(carousel_material)
                ]
            except CarouselBindingValidationError as exc:
                raise ValidationError(str(exc)) from exc

    top_level_product_ids = {str(product_id) for product_id in product_ids}
    if creative_product_ids != top_level_product_ids:
        raise ValidationError(
            "multi_product_creative_list product IDs must exactly match top-level "
            "product_ids."
        )

    media = payload.get("programmatic_creative_media_list")
    if not isinstance(media, dict):
        raise ValidationError(
            "VIDEO_PROM_GOODS UNI_AWEME payload must include "
            "programmatic_creative_media_list."
        )
    required_media_fields = (
        "block_video_material",
        "title_material",
        "video_material",
    )
    for field in required_media_fields:
        items = media.get(field)
        if not isinstance(items, list):
            raise ValidationError(
                f"programmatic_creative_media_list.{field} must be a list."
            )
        if items:
            raise ValidationError(
                f"Guarded automatic-material creation requires empty "
                f"programmatic_creative_media_list.{field}; explicit material rows "
                "need separate material authorization."
            )


def _enforce_uni_product_channels(
    payload: dict[str, Any],
    official_channels: dict[str, dict[str, Any]],
) -> None:
    for item in payload.get("product_channel_info") or []:
        product_id = str(item["product_id"])
        official = official_channels.get(product_id)
        if official is None:
            raise ValidationError(
                f"Product {product_id} channel was not returned by the official "
                "uni-promotion product API."
            )
        if str(item.get("channel_id")) != str(official.get("channel_id")):
            raise ValidationError(
                f"Product {product_id} channel_id does not match official discovery."
            )
        if str(item.get("channel_type")) != str(official.get("channel_type")):
            raise ValidationError(
                f"Product {product_id} channel_type does not match official discovery."
            )


def _enforce_uni_product_card_images(
    payload: dict[str, Any],
    official_product_images: dict[str, set[str]],
) -> None:
    """Validate legacy card images only when an older payload explicitly supplies one."""
    for item in payload.get("multi_product_creative_list") or []:
        if "creative_card" not in item:
            continue
        product_id = str(item["product_id"])
        card = item.get("creative_card") or {}
        image_id = str(card.get("promotion_card_image_id") or "")
        allowed = official_product_images.get(product_id, set())
        if not allowed:
            raise ValidationError(
                f"Product {product_id} has no official square card image in preflight."
            )
        if image_id not in allowed:
            raise ValidationError(
                f"Product {product_id} promotion_card_image_id is not one of its official "
                "uni-promotion product images."
            )


def _qianchuan_image_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if "://" not in text:
        return text
    path = unquote(urlparse(text).path)
    marker = "/obj/"
    if marker not in path:
        return None
    return path.split(marker, 1)[1].lstrip("/") or None


def _enforce_daily_limit(current: int, maximum: int, kind: str) -> None:
    if current >= maximum:
        raise ValidationError(f"Daily {kind} creation limit {maximum} has been reached.")


def _enforce_budget(value: Any, maximum: Decimal, kind: str) -> None:
    if value is None:
        raise ValidationError(f"{kind} create payload must include an explicit budget.")
    try:
        budget = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"{kind} budget is invalid.") from exc
    if budget <= 0 or budget > Decimal(str(maximum)):
        raise ValidationError(f"{kind} budget {budget} is outside 0..{maximum}.")


def _enforce_id_scope(ids: set[str], allowed: set[str], kind: str, *, required: bool) -> None:
    if required and not ids:
        raise ValidationError(f"Launch payload must include at least one {kind}_id.")
    if not ids:
        return
    blocked = ids - allowed
    if blocked:
        raise ValidationError(f"{kind}_id outside launch authorization: {', '.join(sorted(blocked))}.")


def _enforce_enum_scope(value: Any, allowed: list[str], field: str) -> None:
    if value is None:
        raise ValidationError(f"Launch payload must include {field}.")
    if str(value) not in set(allowed or []):
        raise ValidationError(f"{field}={value} is outside launch authorization.")


def _enforce_inventory(ids: set[str], inventory: dict[str, int], minimum: int) -> None:
    missing = ids - set(inventory)
    if missing:
        raise ValidationError(
            "Selected product was not returned by the official available-product API: "
            + ", ".join(sorted(missing))
        )
    insufficient = {item: inventory[item] for item in ids if inventory[item] < minimum}
    if insufficient:
        detail = ", ".join(f"{key}={value}" for key, value in sorted(insufficient.items()))
        raise ValidationError(f"Product inventory is below launch minimum {minimum}: {detail}.")
