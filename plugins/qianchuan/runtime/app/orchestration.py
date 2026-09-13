from __future__ import annotations

from decimal import Decimal

from app.schemas import ActionProposal, BlockedProposal
from app.strategy import ValidationError


DEFAULT_ALLOWED_ACTIONS_BY_ROLE: dict[str, list[str]] = {
    "scale": ["budget_update", "bid_update", "roi_goal_update", "ad_status_update"],
    "test": ["budget_update", "bid_update", "ad_status_update"],
    "retarget": ["budget_update", "bid_update", "roi_goal_update"],
    "hold": [],
}

ACTION_CODE_TO_POLICY_ACTION: dict[str, str] = {
    "ad_budget.update": "budget_update",
    "ad_bid.update": "bid_update",
    "roi_goal.update": "roi_goal_update",
    "ad_status.update": "ad_status_update",
}

TOOL_KEY_TO_POLICY_ACTION: dict[str, str] = {
    "qianchuan_campaign_create_v1": "campaign_create",
    "qianchuan_campaign_update_v1": "campaign_update",
    "qianchuan_ad_create_v1": "ad_create",
    "qianchuan_ad_update_v1": "ad_update",
    "qianchuan_ad_overall_marketing_update_v1": "ad_update",
    "qianchuan_uni_promotion_multiplier_upgrade_v1": "ad_update",
    "qianchuan_ad_region_update_v1": "ad_update",
    "qianchuan_ad_schedule_date_update_v1": "ad_update",
    "qianchuan_ad_schedule_fixed_range_update_v1": "ad_update",
    "qianchuan_batch_campaign_status_update_v1": "campaign_update",
    "qianchuan_account_budget_update_v1": "account_budget_update",
    "qianchuan_ad_material_delete_v1": "material_delete",
    "qianchuan_image_delete_v1": "material_delete",
    "qianchuan_file_video_ad_v2": "material_upload",
    "qianchuan_file_image_ad_v2": "material_upload",
    "qianchuan_carousel_create_v2": "carousel_create",
    "qianchuan_uni_aweme_ad_create_v1": "ad_create",
    "qianchuan_uni_aweme_ad_update_v1": "ad_update",
    "qianchuan_uni_promotion_authorization_apply_v1": "uni_authorization_apply",
    "qianchuan_uni_promotion_ad_budget_update_v1": "budget_update",
    "qianchuan_uni_promotion_ad_roi2_goal_update_v1": "roi_goal_update",
    "qianchuan_uni_promotion_ad_status_update_v1": "ad_status_update",
    "qianchuan_uni_promotion_ad_delete_guarded_v1": "ad_status_update",
    "qianchuan_aweme_order_create_v1": "order_create",
    "qianchuan_aweme_order_budget_add_v1": "order_budget_update",
    "qianchuan_aweme_uni_promotion_order_create_v1": "order_create",
    "qianchuan_aweme_uni_promotion_order_budget_add_v1": "order_budget_update",
    "qianchuan_tools_smart_boost_ad_boost_set_v1": "smart_boost_update",
    "qianchuan_uni_promotion_ad_control_task_create_v1": "control_task_create",
    "qianchuan_uni_promotion_ad_control_task_smart_control_create_v1": "control_task_create",
    "qianchuan_uni_promotion_ad_control_task_update_v1": "control_task_update",
    "qianchuan_uni_promotion_ad_control_task_status_update_v1": "control_task_update",
    "qianchuan_uni_promotion_ad_control_task_budget_update_v1": "control_task_update",
    "qianchuan_uni_promotion_ad_control_task_duration_update_v1": "control_task_update",
    "qianchuan_uni_promotion_ad_control_task_smart_control_status_update_v1": "control_task_update",
    "qianchuan_uni_promotion_ad_name_update_v1": "ad_update",
    "qianchuan_uni_promotion_ad_schedule_date_update_v1": "ad_update",
    "qianchuan_uni_promotion_auth_init_v1": "uni_authorization_apply",
    "qianchuan_uni_promotion_ad_product_delete_v1": "product_delete",
    "qianchuan_uni_promotion_ad_material_add_v1": "material_bind",
    "qianchuan_uni_promotion_ad_material_delete_v1": "material_delete",
    "qianchuan_ad_budget_update_v1": "budget_update",
    "qianchuan_ad_bid_update_v1": "bid_update",
    "qianchuan_roi_goal_update_v1": "roi_goal_update",
    "qianchuan_ad_status_update_v1": "ad_status_update",
}

PROPOSAL_ACTION_TO_POLICY_ACTION: dict[str, str] = {
    "increase_budget": "budget_update",
    "decrease_budget": "budget_update",
    "pause_ad": "ad_status_update",
    "enable_ad": "ad_status_update",
    "update_bid": "bid_update",
    "update_roi_goal": "roi_goal_update",
}


def default_allowed_actions(account_role: str) -> list[str]:
    return list(DEFAULT_ALLOWED_ACTIONS_BY_ROLE.get(account_role, []))


def normalize_allowed_actions(account_role: str, allowed_actions: list[str] | None) -> list[str]:
    if allowed_actions is None:
        return default_allowed_actions(account_role)
    return list(dict.fromkeys(allowed_actions))


def policy_action_for_action_code(action_code: str) -> str:
    if action_code.startswith("tool."):
        return policy_action_for_tool_key(action_code.removeprefix("tool."))
    return ACTION_CODE_TO_POLICY_ACTION.get(action_code, "raw_write")


def policy_action_for_tool_key(tool_key: str) -> str:
    return TOOL_KEY_TO_POLICY_ACTION.get(tool_key, "raw_write")


def enforce_account_policy_for_write(
    *,
    policy,
    action_code: str,
    payload,
    require_policy: bool,
) -> None:
    advertiser_id = payload_advertiser_id(payload)
    if advertiser_id is None:
        raise ValidationError("Write payload must include advertiser_id for account policy enforcement.")
    if policy is None:
        if require_policy:
            raise ValidationError(
                f"Advertiser {advertiser_id} has no account policy. "
                "Create an account policy before enabling AI write actions."
            )
        return
    if not policy.write_enabled:
        raise ValidationError(f"Advertiser {advertiser_id} account policy has write_enabled=false.")
    if policy.account_role == "hold":
        raise ValidationError(f"Advertiser {advertiser_id} account role is hold; writes are blocked.")

    policy_action = policy_action_for_action_code(action_code)
    if policy_action not in set(policy.allowed_actions or []):
        raise ValidationError(
            f"Action {policy_action} is not allowed for advertiser {advertiser_id} "
            f"role={policy.account_role}."
        )
    _enforce_ad_whitelist(
        policy=policy,
        payload=payload,
        require_ids=policy_action in {"ad_update", "material_bind", "material_delete", "control_task_create"},
    )
    _enforce_material_whitelist(
        policy=policy,
        payload=payload,
        require_ids=policy_action in {"ad_create", "material_bind", "material_delete", "carousel_create"},
    )
    _enforce_product_whitelist(
        policy=policy,
        payload=payload,
        require_ids=policy_action == "ad_create",
    )
    if policy_action == "budget_update":
        _enforce_budget_change_rate(payload, Decimal(str(policy.max_daily_budget_change_rate)))


def filter_proposals_by_policy(
    *,
    policy,
    proposals: list[ActionProposal],
    require_policy: bool,
) -> tuple[list[ActionProposal], list[BlockedProposal], list[str]]:
    warnings: list[str] = []
    if policy is None:
        reason = "No account policy exists; proposal requires manual policy setup."
        if require_policy:
            return [], [BlockedProposal(proposal=item, reason=reason) for item in proposals], [reason]
        return proposals, [], []
    allowed_actions = set(policy.allowed_actions or [])
    if not policy.write_enabled:
        warnings.append("Account policy write_enabled=false; proposals are advisory only.")
    approved: list[ActionProposal] = []
    blocked: list[BlockedProposal] = []
    for proposal in proposals:
        policy_action = PROPOSAL_ACTION_TO_POLICY_ACTION.get(proposal.action_type, "raw_write")
        if not policy.write_enabled:
            blocked.append(BlockedProposal(proposal=proposal, reason="Account policy disables writes."))
        elif policy.account_role == "hold":
            blocked.append(BlockedProposal(proposal=proposal, reason="Account role is hold."))
        elif policy_action not in allowed_actions:
            blocked.append(
                BlockedProposal(
                    proposal=proposal,
                    reason=f"Action {policy_action} is not allowed by account policy.",
                )
            )
        elif not proposal_ad_ids_allowed(policy, proposal):
            blocked.append(
                BlockedProposal(
                    proposal=proposal,
                    reason="Proposal touches ad_id outside account policy allowed_ad_ids.",
                )
            )
        else:
            approved.append(proposal)
    return approved, blocked, warnings


def payload_advertiser_id(payload) -> int | None:
    if isinstance(payload, dict):
        value = payload.get("advertiser_id")
    else:
        value = getattr(payload, "advertiser_id", None)
    return int(value) if value is not None else None


def payload_object_ids(payload) -> set[str]:
    recursive_ids = _extract_ids_from_payload(
        payload,
        {"ad_id", "ad_ids", "video_signature"},
    )
    if recursive_ids:
        return recursive_ids
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return {str(item["ad_id"]) for item in data if isinstance(item, dict) and item.get("ad_id") is not None}
        ad_ids = payload.get("ad_ids")
        if isinstance(ad_ids, list):
            return {str(item) for item in ad_ids}
        return set()
    if hasattr(payload, "data"):
        return {
            str(item.ad_id)
            for item in payload.data
            if getattr(item, "ad_id", None) is not None
        }
    if hasattr(payload, "ad_ids"):
        return {str(item) for item in payload.ad_ids}
    return set()


def payload_material_ids(payload) -> set[str]:
    return _extract_ids_from_payload(
        payload,
        {
            "material_id",
            "material_ids",
            "creative_id",
            "creative_ids",
            "video_id",
            "video_ids",
            "image_id",
            "image_ids",
            "carousel_id",
            "aweme_carousel_id",
        },
    )


def payload_product_ids(payload) -> set[str]:
    return _extract_ids_from_payload(payload, {"product_id", "product_ids"})


def payload_aweme_ids(payload) -> set[str]:
    return _extract_ids_from_payload(payload, {"aweme_id", "aweme_ids"})


def proposal_ad_ids_allowed(policy, proposal: ActionProposal) -> bool:
    allowed = set(policy.allowed_ad_ids or [])
    if not allowed:
        return True
    ids = set()
    if proposal.object_type == "ad" and proposal.object_id:
        ids.add(str(proposal.object_id))
    ids.update(payload_object_ids(proposal.payload))
    return bool(ids) and ids.issubset(allowed)


def material_id_allowed(policy, material_id: str) -> bool:
    allowed = set(policy.allowed_material_ids or [])
    return not allowed or str(material_id) in allowed


def _enforce_ad_whitelist(*, policy, payload, require_ids: bool = False) -> None:
    allowed = set(policy.allowed_ad_ids or [])
    if not allowed:
        return
    ids = payload_object_ids(payload)
    if not ids:
        if require_ids:
            raise ValidationError("Write payload must include ad_id/ad_ids when allowed_ad_ids is configured.")
        return
    blocked = ids - allowed
    if blocked:
        joined = ", ".join(sorted(blocked))
        raise ValidationError(f"ad_id outside account policy allowed_ad_ids: {joined}.")


def _enforce_material_whitelist(*, policy, payload, require_ids: bool = False) -> None:
    allowed = set(policy.allowed_material_ids or [])
    if not allowed:
        return
    ids = payload_material_ids(payload)
    if not ids:
        if require_ids:
            raise ValidationError(
                "Write payload must include material_id/material_ids/creative_id/creative_ids "
                "when allowed_material_ids is configured."
            )
        return
    blocked = ids - allowed
    if blocked:
        joined = ", ".join(sorted(blocked))
        raise ValidationError(f"material_id outside account policy allowed_material_ids: {joined}.")


def _enforce_product_whitelist(*, policy, payload, require_ids: bool = False) -> None:
    allowed = set(policy.allowed_product_ids or [])
    if not allowed:
        return
    ids = payload_product_ids(payload)
    if not ids:
        if require_ids:
            raise ValidationError(
                "Ad create payload must include product_id/product_ids when "
                "allowed_product_ids is configured."
            )
        return
    blocked = ids - allowed
    if blocked:
        joined = ", ".join(sorted(blocked))
        raise ValidationError(f"product_id outside account policy allowed_product_ids: {joined}.")


def _extract_ids_from_payload(payload, keys: set[str]) -> set[str]:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys:
                if isinstance(value, list):
                    found.update(str(item) for item in value if item not in (None, ""))
                elif value not in (None, ""):
                    found.add(str(value))
            else:
                found.update(_extract_ids_from_payload(value, keys))
    elif isinstance(payload, list):
        for item in payload:
            found.update(_extract_ids_from_payload(item, keys))
    return found


def _enforce_budget_change_rate(payload, max_rate: Decimal) -> None:
    if max_rate <= 0:
        return
    if isinstance(payload, dict):
        items = payload.get("data")
        if items is None:
            items = payload.get("update_budget_infos", [])
    else:
        items = getattr(payload, "data", None)
        if items is None:
            items = getattr(payload, "update_budget_infos", [])
    for item in items:
        ad_id = item.get("ad_id") if isinstance(item, dict) else item.ad_id
        budget = Decimal(str(item.get("budget") if isinstance(item, dict) else item.budget))
        previous_budget_value = item.get("previous_budget") if isinstance(item, dict) else item.previous_budget
        if previous_budget_value is None:
            raise ValidationError(
                f"Budget update for ad {ad_id} requires previous_budget because account policy "
                "has max_daily_budget_change_rate."
            )
        previous_budget = Decimal(str(previous_budget_value))
        if previous_budget <= 0:
            raise ValidationError(f"Budget update for ad {ad_id} has invalid previous_budget.")
        change_rate = abs(budget - previous_budget) / previous_budget
        if change_rate > max_rate:
            raise ValidationError(
                f"Budget update for ad {ad_id} changes {change_rate:.2%}, above account policy limit {max_rate:.2%}."
            )
