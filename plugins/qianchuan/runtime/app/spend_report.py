"""Account spend only: no topic guessing, no ad writes, no double-counted dimensions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from threading import Lock
import time
import uuid

from app.qianchuan_client import QianchuanApiError

PATH = "/v1.0/qianchuan/report/all_promotion/get/"
SCENES = ("UNI_PROJECT", "OVERALL_PROJECT")
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qianchuan-read-report")
_lock = Lock()
_jobs = {}


def guide():
    return {
        "default_for_yesterday_or_account_spend": "qianchuan_start_spend_report",
        "poll": "qianchuan_get_spend_report_result",
        "scope": "PC千川全域+乘方，不含随心推、历史标准投放；不是财务账单",
        "routes": [
            {"purpose": "账户/所有授权账户按日消耗", "endpoint": PATH,
             "metric": "stat_cost_for_roi2", "unit": "CNY元",
             "scenes": {"UNI_PROJECT": "全域计划", "OVERALL_PROJECT": "乘方计划"},
             "tool": "qianchuan_start_spend_report"},
            {"purpose": "商品全域商品/素材/项目细分", "tool": "qianchuan_get_uni_promotion_report_config",
             "topics": "OVERALL_ROI_PRODUCT_*、SITE_PROMOTION_PRODUCT_*；以官方配置返回为准",
             "rule": "先查配置再选一个对应粒度主题，不能扫描全部主题后相加；商品推广不等于纯商品卡流量"},
            {"purpose": "直播全域抖音号/直播素材/视频素材", "tool": "qianchuan_get_live_uni_report_config",
             "topics": "OVERALL_ROI_LIVE_*", "rule": "不是标准直播报表"},
            {"purpose": "历史标准账户/计划报表", "tool": "qianchuan_get_account_report",
             "metric": "stat_cost", "rule": "空结果不能证明全域无消耗，不用于当前全域总消耗"},
            {"purpose": "审核建议", "tool": "qianchuan_get_uni_promotion_ad_suggestions",
             "rule": "不是完整流量诊断，不自动执行建议"},
            {"purpose": "旧全域汇总", "endpoint": "/v1.0/qianchuan/report/uni_promotion/get/",
             "rule": "兼容保留；当前总消耗用all_promotion/get，遇50000不要随机换指标"},
        ],
        "rules": ["stat_cost和stat_cost_for_roi2不得随意互换", "请求失败或字段缺失是未知，不是0",
                  "不同主题/维度可能描述同一笔消耗，禁止相加", "不把综合成本stat_cost_for_overall_roi2当广告消耗",
                  "ROI=同口径成交额合计/消耗合计，不平均各行ROI", "不把商品全域总额标记成纯商品卡消耗"],
    }


def validate_dates(start_date, end_date):
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    today = datetime.now(timezone(timedelta(hours=8))).date()
    if end < start or (end - start).days >= 31 or end > today:
        raise ValueError("Use ordered dates, at most 31 calendar days, no future date (Asia/Shanghai).")
    return start.isoformat(), end.isoformat()


def summarize(rows, expected):
    known = sum((Decimal(p["cost_yuan"]) for r in rows for p in r["parts"] if p["status"] == "ok"), Decimal(0))
    complete = len(rows) == expected and all(r["status"] == "ok" for r in rows)
    return {"complete": complete, "account_count": expected, "processed_accounts": len(rows),
            "successful_accounts": sum(r["status"] == "ok" for r in rows),
            "failed_or_partial_accounts": sum(r["status"] != "ok" for r in rows),
            "total_cost_yuan": str(known) if complete else None,
            "known_cost_yuan": str(known), "rows": deepcopy(rows)}


def collect_account(client, token, account, start, end, goal):
    parts = []
    for scene in SCENES:
        try:
            response = client.get(PATH, access_token=token, params={
                "advertiser_id": account["advertiser_id"], "start_time": start + " 00:00:00",
                "end_time": end + " 23:59:59", "adlab_scene": scene,
                "fields": ["stat_cost_for_roi2"], "marketing_goal": goal, "order_platform": "QIANCHUAN"})
            data = response.get("data")
            if not isinstance(data, dict):
                raise ValueError("missing_report_object")
            raw = data.get("stat_cost_for_roi2")
            if raw is None or isinstance(raw, bool):
                raise ValueError("missing_cost_metric")
            value = Decimal(str(raw))
            if not value.is_finite() or value < 0:
                raise ValueError("invalid_cost_metric")
            parts.append({"scene": scene, "status": "ok", "cost_yuan": str(value),
                          "request_id": response.get("request_id")})
        except QianchuanApiError as exc:
            parts.append({"scene": scene, "status": "error", "cost_yuan": None,
                          "code": exc.code, "http_status": exc.status_code, "request_id": exc.request_id})
        except (ValueError, InvalidOperation):
            parts.append({"scene": scene, "status": "unknown", "cost_yuan": None,
                          "reason": "missing_or_invalid_cost_metric", "request_id": response.get("request_id")})
    ok = all(p["status"] == "ok" for p in parts)
    return {**account, "status": "ok" if ok else "partial", "parts": parts,
            "cost_yuan": str(sum((Decimal(p["cost_yuan"]) for p in parts), Decimal(0))) if ok else None}


def start_report(client, token, accounts, start_date, end_date, goal="ALL"):
    start, end = validate_dates(start_date, end_date)
    if goal not in ("ALL", "LIVE_PROM_GOODS", "VIDEO_PROM_GOODS"):
        raise ValueError("Unsupported marketing_goal")
    accounts = list({str(a["advertiser_id"]): a for a in accounts}.values())
    if not accounts or len(accounts) > 200:
        raise ValueError("Require 1..200 resolved authorized accounts")
    key = (start, end, goal, tuple(sorted(str(a["advertiser_id"]) for a in accounts)))
    with _lock:
        for job_id in list(_jobs):
            if _jobs[job_id]["state"] != "running" and time.monotonic() - _jobs[job_id]["created"] > 1800:
                del _jobs[job_id]
        for job_id, job in _jobs.items():
            if job["key"] == key and job["state"] == "running":
                return {"status": "running", "job_id": job_id, "deduplicated": True, "next_tool": "qianchuan_get_spend_report_result"}
        if sum(j["state"] == "running" for j in _jobs.values()) >= 2 or len(_jobs) >= 20:
            return {"status": "busy", "reason": "report_job_limit", "retry_after_seconds": 10}
        job_id = "qcr_" + uuid.uuid4().hex
        _jobs[job_id] = {"created": time.monotonic(), "key": key, "state": "running", "rows": [],
                         "expected": len(accounts), "start_date": start, "end_date": end, "marketing_goal": goal}
    def run():
        try:
            for account in accounts:
                row = collect_account(client, token, account, start, end, goal)
                with _lock:
                    _jobs[job_id]["rows"].append(row)
            with _lock:
                _jobs[job_id]["state"] = "finished"
        except Exception:
            with _lock:
                _jobs[job_id]["state"] = "failed"
    _pool.submit(run)
    return {"status": "running", "job_id": job_id, "account_count": len(accounts),
            "poll_after_seconds": 10, "next_tool": "qianchuan_get_spend_report_result",
            "scope": guide()["scope"], "writes_submitted": False}


def result(job_id):
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return {"status": "not_found", "reason": "job_expired_or_process_restarted", "complete": False}
        summary = summarize(job["rows"], job["expected"])
        status = "running" if job["state"] == "running" else ("complete" if summary["complete"] else "partial")
        return {"status": status, "job_id": job_id, **summary, "start_date": job["start_date"],
                "end_date": job["end_date"], "timezone": "Asia/Shanghai", "marketing_goal": job["marketing_goal"],
                "scope": guide()["scope"], "metric": "stat_cost_for_roi2", "currency": "CNY",
                "endpoint": PATH, "writes_submitted": False, "poll_after_seconds": 10 if status == "running" else None}
