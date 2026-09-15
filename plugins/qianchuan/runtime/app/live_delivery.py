"""Offline live-delivery diagnostics. Does not call an API or generate write payloads."""
from datetime import date
from decimal import Decimal, InvalidOperation


def report_params(advertiser_id, start_date, end_date, ad_ids, page=1, page_size=100):
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if start > end or (end - start).days > 30:
        raise ValueError("Date range must be ordered and at most 31 calendar days")
    if advertiser_id <= 0 or page < 1 or not 1 <= page_size <= 100:
        raise ValueError("Invalid advertiser_id or pagination")
    if not ad_ids or len(ad_ids) > 100 or any(i <= 0 for i in ad_ids):
        raise ValueError("Provide 1-100 exact positive plan IDs")
    return {"advertiser_id": advertiser_id, "start_date": start_date, "end_date": end_date,
            "page": page, "page_size": page_size,
            "filtering": {"ad_ids": sorted(set(ad_ids)), "marketing_goal": "LIVE_PROM_GOODS", "order_platform": "QIANCHUAN"}}


def analyze(snapshot):
    """Use caller-supplied normalized facts; money units must be yuan, not raw API units."""
    required = ["advertiser_id", "ad_id", "marketing_goal", "window_start", "window_end",
                "stat_cost_yuan", "pay_amount_yuan", "paid_orders", "attribution_mature"]
    missing = [k for k in required if snapshot.get(k) is None]
    if missing:
        return {"status": "input_required", "missing_fields": missing, "actions": []}
    if snapshot["marketing_goal"] != "LIVE_PROM_GOODS":
        raise ValueError("This diagnostic accepts only LIVE_PROM_GOODS")
    from datetime import datetime
    start = datetime.fromisoformat(snapshot["window_start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(snapshot["window_end"].replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("Use an ordered timezone-aware observation window")
    values = []
    for key in ("stat_cost_yuan", "pay_amount_yuan", "paid_orders"):
        try:
            if isinstance(snapshot[key], bool):
                raise ValueError("Boolean is not a metric")
            number = Decimal(str(snapshot[key]))
            if not number.is_finite() or number < 0:
                raise ValueError("Metrics must be finite and nonnegative")
            values.append(number)
        except InvalidOperation as exc:
            raise ValueError("Metrics must be numeric") from exc
    spend, gmv, orders = values
    if orders != orders.to_integral_value() or not isinstance(snapshot["attribution_mature"], bool):
        raise ValueError("paid_orders must be integer; attribution_mature must be boolean")
    findings = []
    if snapshot.get("plan_status") == "LIVE_ROOM_OFF":
        findings.append("计划提示未开播；先确认直播状态，不以降ROI替代排查。")
    if snapshot.get("audit_rejected") is True:
        findings.append("存在审核拒绝标记；先处理审核原因，不建议增加预算。")
    if spend == 0:
        findings.append("无消耗：检查开播、排期、审核、余额、预算和素材资格；无法由零消耗推断ROI过高。")
    if not snapshot["attribution_mature"]:
        findings.append("归因未成熟，仅作过程观察，不据此确定最终投产。")
    if spend > 0 and orders == 0:
        findings.append("已消耗但无订单；结合成熟窗口、直播转化和素材数据排查，不自动放量。")
    return {"status": "analyzed", "mode": "offline", "advertiser_id": str(snapshot["advertiser_id"]),
            "ad_id": str(snapshot["ad_id"]), "roi": str(gmv / spend) if spend else None,
            "roi_basis": "provided_pay_amount_yuan / provided_stat_cost_yuan; not net profit",
            "attribution_mature": snapshot["attribution_mature"], "findings": findings,
            "actions": [], "official_state_verified": False,
            "message": "仅分析提供的快照，不代表已核验实时直播，不执行或自动生成投放写操作。"}
