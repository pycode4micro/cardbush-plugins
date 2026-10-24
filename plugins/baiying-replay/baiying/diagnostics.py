"""Safe, structured failures shared by the MCP server and background workers.

Never persist request arguments, response bodies, exception reprs or traceback
source lines: these can contain login tickets, cookies and signed media URLs.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import subprocess
import time
import traceback
from pathlib import Path

import requests


MESSAGES = {
    "authentication_required": ("百应登录失效或尚未登录，请检查登录状态。", "check_login", False),
    "permission_denied": ("百应拒绝访问，请核对所选账号的权限。", "check_account_access", False),
    "rate_limited": ("百应请求过于频繁，请稍后再查询。", "retry_query_later", True),
    "provider_unavailable": ("百应接口暂时异常，请保留当前范围并稍后重试查询。", "retry_query_later", True),
    "provider_rejected": ("百应拒绝了请求，请依据错误阶段和服务端错误码排查。", "inspect_diagnostic", False),
    "invalid_response": ("百应返回了无法解析或缺少必要字段的响应，不能视为没有素材。", "inspect_diagnostic", False),
    "timeout": ("百应请求超时，未能获得完整响应。", "retry_query_later", True),
    "connection_error": ("连接百应失败，请检查网络或代理。", "check_network_then_retry", True),
    "tls_error": ("百应连接的证书校验失败，请检查证书或代理配置。", "check_network_configuration", False),
    "http_error": ("百应 HTTP 请求失败，请依据状态码和请求阶段排查。", "inspect_diagnostic", False),
    "invalid_arguments": ("参数或本地前置条件不符合要求。", "fix_arguments_or_preconditions", False),
    "storage_busy": ("本地任务数据库正忙，请稍后重试读取。", "retry_query_later", True),
    "storage_error": ("本地记录或文件读写失败，请检查磁盘、路径、权限或 NAS 连接。", "check_local_storage", False),
    "media_error": ("媒体检查进程失败，请检查 FFmpeg 和素材文件。", "check_media_dependencies", False),
    "internal_error": ("百应插件发生内部错误，请提供诊断编号排查。", "inspect_diagnostic", False),
}


def _identifier(value, maximum=128):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    value = str(value)
    return value if re.fullmatch(r"[A-Za-z0-9_.:-]{1," + str(maximum) + r"}", value) else None


def _provider_code(value):
    # Business codes are numeric; do not echo arbitrary strings from a response.
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,12}", value):
        return value
    return None


def provider_category(code, message):
    text = str(message or "").lower()
    if str(code) == "401" or any(x in text for x in ("登录失效", "未登录", "登录过期", "login expired", "not logged in")):
        return "authentication_required"
    if str(code) == "403" or any(x in text for x in ("无权限", "没有权限", "permission denied")):
        return "permission_denied"
    if str(code) == "429" or any(x in text for x in ("请求频繁", "操作频繁", "too many requests")):
        return "rate_limited"
    if str(code) in {"500", "502", "503", "504"} or any(x in text for x in ("下游服务内部异常", "系统繁忙", "服务异常", "稍后重试")):
        return "provider_unavailable"
    return "provider_rejected"


def response_metadata(response):
    headers = getattr(response, "headers", {}) or {}
    request_id = next((_identifier(headers.get(k)) for k in
                       ("x-tt-logid", "X-Tt-Logid", "x-request-id", "X-Request-Id")
                       if _identifier(headers.get(k))), None)
    status = getattr(response, "status_code", None)
    return {"http_status": status if type(status) is int else None, "provider_request_id": request_id}


class ProviderError(RuntimeError):
    def __init__(self, step, code=None, message=None, *, category=None, response=None):
        self.step = _identifier(step)
        self.code = _provider_code(code)
        self.category = category or provider_category(code, message)
        self.retryable = MESSAGES[self.category][2]
        self.metadata = response_metadata(response)
        # The raw provider message is deliberately not retained in exception args.
        super().__init__(f"{self.step}: {MESSAGES[self.category][0]}")


def invalid_response(step, response=None):
    return ProviderError(step, category="invalid_response", response=response)


def _validation_message(exc):
    # Only local validation messages are shown. Reject strings that look like a
    # body, credential assignment or URL instead of trying to redact unknown keys.
    text = str(exc)
    if (len(text) > 350 or any(c in text for c in ("\n", "\r", "{", "}", "=", "://"))
            or re.search(r"\b\d{6,}\b|\b(cookie|token|ticket|authorization)\s*[:=]", text, re.I)):
        return MESSAGES["invalid_arguments"][0]
    return text or MESSAGES["invalid_arguments"][0]


def describe_error(exc, *, operation, read_only=False, stage=None, next_action=None):
    metadata = {}
    if isinstance(exc, ProviderError):
        category = exc.category
        metadata = {"provider_code": exc.code, "request_stage": exc.step, **exc.metadata}
        stage = stage or exc.step
    elif isinstance(exc, requests.exceptions.SSLError):
        category = "tls_error"
    elif isinstance(exc, requests.Timeout):
        category = "timeout"
    elif isinstance(exc, requests.ConnectionError):
        category = "connection_error"
    elif isinstance(exc, (requests.exceptions.JSONDecodeError, json.JSONDecodeError)):
        category = "invalid_response"
    elif isinstance(exc, requests.HTTPError):
        metadata = response_metadata(exc.response)
        status = metadata["http_status"]
        category = ({401: "authentication_required", 403: "permission_denied", 429: "rate_limited"}.get(status)
                    or ("provider_unavailable" if isinstance(status, int) and 500 <= status <= 599 else "http_error"))
        stage = stage or getattr(exc, "baiying_step", None)
        metadata["request_stage"] = _identifier(getattr(exc, "baiying_step", None))
    elif isinstance(exc, requests.RequestException):
        category = "http_error"
    elif isinstance(exc, sqlite3.Error):
        code = getattr(exc, "sqlite_errorcode", None)
        busy = code is not None and (code & 0xff) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
        category = "storage_busy" if busy or str(exc).lower() in {"database is locked", "database table is locked"} else "storage_error"
    elif isinstance(exc, OSError):
        category = "storage_error"
        metadata = {"os_errno": exc.errno if isinstance(exc.errno, int) else None}
    elif isinstance(exc, subprocess.SubprocessError):
        category = "media_error"
    elif isinstance(exc, ValueError):
        category = "authentication_required" if str(exc) in {
            "请先用手机号完成百应登录。",
            "no login-derived cached Baiying cookies; run login for this mobile/account first",
            "no login-derived cached Baiying cookies; run login first",
            "no platform Cookie from SMS login; run login for this mobile first",
        } else "invalid_arguments"
    else:
        category = "internal_error"
    message, action, transient = MESSAGES[category]
    if category == "invalid_arguments":
        message = _validation_message(exc)
    if not read_only:
        action = "check_existing_state_before_retry"
    return {"diagnostic_id": "byerr_" + secrets.token_hex(12), "category": category,
            "message": message, "operation": _identifier(operation), "stage": _identifier(stage),
            "exception_type": type(exc).__name__, "transient": transient,
            "retryable": bool(read_only and transient), "next_action": next_action or action,
            **{key: value for key, value in metadata.items() if value is not None}}


def record_error(exc, *, operation, read_only=False, stage=None, next_action=None):
    detail = describe_error(exc, operation=operation, read_only=read_only, stage=stage, next_action=next_action)
    data = Path(os.environ.get("BAIYING_DATA_DIR") or
                (Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local/share")) / "baiying-replay"))
    # One exclusive file per diagnostic works across MCP and worker processes.
    target = data / "diagnostics" / (detail["diagnostic_id"] + ".json")
    record = {"schema_version": "baiying-error/v1", "created_at": int(time.time()), **detail,
              "stack": [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                        for frame in traceback.extract_tb(exc.__traceback__)[-10:]]}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
        detail.update(diagnostic_saved=True, diagnostic_file=str(target))
    except OSError:
        # A logging failure must not replace the original error or break stdout MCP.
        detail.update(diagnostic_saved=False)
    return detail
