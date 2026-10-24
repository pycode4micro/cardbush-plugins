from __future__ import annotations

import errno
import json
import sqlite3
from pathlib import Path

import pytest
import requests

from baiying import client, diagnostics
import server


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BAIYING_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BAIYING_COOKIE_CACHE_PATH", str(tmp_path / "cookies.sqlite3"))
    monkeypatch.setenv("BAIYING_REPLAY_STATE_PATH", str(tmp_path / "state.sqlite3"))


def response(status=200, body=None):
    result = requests.Response()
    result.status_code = status
    result.url = "https://example.test/callback?ticket=secret-ticket&signature=secret-signature"
    result.headers["x-tt-logid"] = "trace-provider-123"
    result._content = json.dumps(body).encode() if body is not None else b"<html>secret-body</html>"
    return result


@pytest.mark.parametrize("exc,category,transient", [
    (requests.Timeout("https://example.test/?ticket=secret-ticket"), "timeout", True),
    (requests.ConnectionError("cookie=secret-cookie"), "connection_error", True),
    (requests.exceptions.SSLError("token=secret-token"), "tls_error", False),
    (requests.HTTPError(response=response(401)), "authentication_required", False),
    (requests.HTTPError(response=response(403)), "permission_denied", False),
    (requests.HTTPError(response=response(429)), "rate_limited", True),
    (requests.HTTPError(response=response(503)), "provider_unavailable", True),
    (requests.HTTPError(response=response(404)), "http_error", False),
    (sqlite3.OperationalError("database is locked"), "storage_busy", True),
    (sqlite3.DatabaseError("database disk image is malformed"), "storage_error", False),
    (OSError(errno.ENOSPC, "full"), "storage_error", False),
    (RuntimeError("secret-internal-data"), "internal_error", False),
])
def test_classification_has_actionable_metadata_without_exception_text(exc, category, transient):
    result = diagnostics.record_error(exc, operation="baiying_search_products", read_only=True)
    assert result["category"] == category
    assert result["retryable"] == transient and result["transient"] == transient
    assert result["next_action"]
    logged = Path(result["diagnostic_file"]).read_text("utf-8")
    assert "secret-" not in logged and "example.test" not in logged
    assert json.loads(logged)["diagnostic_id"] == result["diagnostic_id"]


@pytest.mark.parametrize("code,message,category,retryable", [
    (500, "下游服务内部异常 cookie=secret-cookie", "provider_unavailable", True),
    (401, "登录失效", "authentication_required", False),
    (403, "无权限", "permission_denied", False),
    (429, "请求频繁", "rate_limited", True),
    (12345, "secret-provider-data", "provider_rejected", False),
])
def test_provider_business_errors_preserve_code_and_stage_only(code, message, category, retryable):
    error = client.ReplayServiceError(code, message, response=response())
    detail = diagnostics.record_error(error, operation="baiying_search_products", read_only=True)
    assert detail["provider_code"] == code and detail["category"] == category
    assert detail["retryable"] == retryable
    assert detail["request_stage"] == "baiying.smart_video_list"
    assert detail["provider_request_id"] == "trace-provider-123" and detail["http_status"] == 200
    assert "secret-" not in str(error) + json.dumps(detail) + Path(detail["diagnostic_file"]).read_text("utf-8")


@pytest.mark.parametrize("body", [None, [], "wrong shape"])
def test_non_json_and_wrong_shape_have_no_raw_body(body):
    with pytest.raises(diagnostics.ProviderError) as caught:
        client.parse_json(response(body=body), "baiying.smart_video_list")
    detail = diagnostics.record_error(caught.value, operation="baiying_search_products", read_only=True)
    assert detail["category"] == "invalid_response" and not detail["retryable"]
    assert "secret-body" not in Path(detail["diagnostic_file"]).read_text("utf-8")


def test_http_error_preserves_endpoint_stage():
    with pytest.raises(requests.HTTPError) as caught:
        client.parse_json(response(503), "baiying.get_login_subject")
    detail = diagnostics.describe_error(caught.value, operation="baiying_search_products", read_only=True)
    assert detail["request_stage"] == "baiying.get_login_subject"
    assert detail["provider_request_id"] == "trace-provider-123"


def test_submit_timeout_never_invites_automatic_resubmission():
    detail = diagnostics.record_error(requests.Timeout("secret"), operation="baiying_start_clips")
    assert detail["transient"] and not detail["retryable"]
    assert detail["next_action"] == "check_existing_state_before_retry"


def test_logging_failure_preserves_original_error(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("BAIYING_DATA_DIR", str(blocker))
    result = diagnostics.record_error(requests.Timeout(), operation="baiying_search_products", read_only=True)
    assert result["category"] == "timeout" and result["diagnostic_saved"] is False
    assert "diagnostic_file" not in result


@pytest.mark.parametrize("message", [
    "verification_code=123456 mobile=13800000000",
    "https://example.test/?token=secret",
    '{"Cookie": "unlabelled-secret"}',
])
def test_validation_does_not_echo_sensitive_values(message):
    detail = diagnostics.record_error(ValueError(message), operation="baiying_complete_login")
    assert message not in detail["message"]
    text = json.dumps(detail) + Path(detail["diagnostic_file"]).read_text("utf-8")
    assert "123456" not in text and "13800000000" not in text and "secret" not in text


def test_local_validation_remains_actionable():
    detail = diagnostics.describe_error(ValueError("参数 offset 格式不正确。"), operation="baiying_material_status", read_only=True)
    assert detail["message"] == "参数 offset 格式不正确。"
    assert not detail["retryable"]


@pytest.mark.parametrize("tool,read_only", [("baiying_search_products", True), ("baiying_start_clips", False)])
def test_mcp_returns_same_structured_and_text_error_without_retrying(tool, read_only, monkeypatch, capsys):
    calls = []

    def fail(_name, _args):
        calls.append(1)
        raise requests.Timeout("ticket=secret-ticket")

    monkeypatch.setattr(server, "call_tool", fail)
    reply = server.handle({"jsonrpc": "2.0", "id": 17, "method": "tools/call", "params": {"name": tool, "arguments": {}}})
    result = reply["result"]
    assert result["isError"] is True and result["structuredContent"]["ok"] is False
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert result["structuredContent"]["error"]["retryable"] is read_only
    assert len(calls) == 1 and capsys.readouterr().out == ""


def test_mcp_business_failure_is_not_generic_or_empty_success(monkeypatch):
    def fail(_name, _args):
        raise server.replay_service.client.ReplayServiceError(500, "下游服务内部异常")

    monkeypatch.setattr(server, "call_tool", fail)
    result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "baiying_search_products", "arguments": {"account_id": "111", "query": "KST1606"}}})["result"]
    detail = result["structuredContent"]["error"]
    assert result["isError"] and detail["category"] == "provider_unavailable"
    assert detail["provider_code"] == 500 and detail["retryable"]
    assert "登录" not in detail["message"] and "权限" not in detail["message"]
