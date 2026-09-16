import asyncio
import base64
import hashlib
import hmac
import json

import httpx
import pytest

from feishu_bot.api import FeishuAPI
from feishu_bot.client import FeishuClient, FeishuError
from feishu_bot.settings import CapabilityDisabled, Settings


def ok(data=None):
    return httpx.Response(200, json={"code": 0, "data": data or {}})


def make_client(handler, **kwargs):
    return FeishuClient(Settings(tenant_access_token="test-token", **kwargs), transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/open-apis/im/v1/messages"),
        ("DELETE", "/open-apis/im/v1/messages/om_test"),
        ("POST", "/open-apis/sheets/v2/spreadsheets/test/values_append"),
        ("PUT", "/open-apis/sheets/v2/spreadsheets/test/values"),
        ("POST", "/open-apis/sheets/v2/spreadsheets/test/sheets_batch_update"),
        ("POST", "/open-apis/bitable/v1/apps/test/tables/tbl_test/records/batch_create"),
        ("POST", "/open-apis/bitable/v1/apps/test/tables/tbl_test/records/batch_update"),
        ("POST", "/open-apis/bitable/v1/apps/test/tables/tbl_test/records/batch_delete"),
    ],
)
async def test_disabled_writes_do_not_authenticate_or_send(method, path):
    requests = []
    client = FeishuClient(
        Settings(app_id="app", app_secret="secret"), transport=httpx.MockTransport(lambda r: requests.append(r) or ok())
    )
    try:
        with pytest.raises(CapabilityDisabled):
            await client.request(method, path, body={})
        with pytest.raises(CapabilityDisabled):
            await client.webhook({"msg_type": "text"})
        assert not requests
    finally:
        await client.close()


async def test_read_only_post_search_and_pagination_preserved():
    requests = []
    client = make_client(
        lambda r: requests.append(r) or ok({"items": [{"record_id": "rec1"}], "has_more": True, "page_token": "next"})
    )
    try:
        result = await FeishuAPI(client).bitable_records_search("app", "tbl", page_size=500, field_names=["编码"])
        assert result["has_more"] and result["page_token"] == "next"
        assert requests[0].method == "POST"
        assert requests[0].url.params["page_size"] == "500"
        assert json.loads(requests[0].content) == {"field_names": ["编码"]}
    finally:
        await client.close()


async def test_read_disabled_also_blocks_post_search():
    requests = []
    client = make_client(lambda r: requests.append(r) or ok(), allow_read=False, allow_write=True)
    try:
        with pytest.raises(CapabilityDisabled):
            await FeishuAPI(client).bitable_records_search("app", "tbl")
        assert not requests
        await FeishuAPI(client).sheet_write("app", "sheet!A1:A1", [["001"]])
        assert len(requests) == 1
    finally:
        await client.close()


async def test_token_cache_and_concurrent_requests():
    requests = []

    async def handler(request):
        requests.append(request)
        if "tenant_access_token" in request.url.path:
            await asyncio.sleep(0)
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "cached-token", "expire": 7200})
        assert request.headers["Authorization"] == "Bearer cached-token"
        return ok()

    client = FeishuClient(Settings(app_id="app", app_secret="secret"), transport=httpx.MockTransport(handler))
    try:
        await asyncio.gather(*(FeishuAPI(client).chat_get(f"oc_{i}") for i in range(3)))
        assert sum("tenant_access_token" in r.url.path for r in requests) == 1
    finally:
        await client.close()


async def test_expired_token_refresh_once_on_read():
    auth_calls = 0

    def handler(request):
        nonlocal auth_calls
        if "tenant_access_token" in request.url.path:
            auth_calls += 1
            return httpx.Response(200, json={"code": 0, "tenant_access_token": f"token{auth_calls}", "expire": 7200})
        if request.headers["Authorization"] == "Bearer token1":
            return httpx.Response(200, json={"code": 99991663, "msg": "expired"})
        return ok({"renewed": True})

    client = FeishuClient(Settings(app_id="app", app_secret="secret"), transport=httpx.MockTransport(handler))
    try:
        assert (await FeishuAPI(client).bot_info())["renewed"]
        assert auth_calls == 2
    finally:
        await client.close()


@pytest.mark.parametrize("failure", ["timeout", "500", "invalid_json"])
async def test_write_uncertainty_is_not_retried(failure):
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-token-in-transport", request=request)
        if failure == "500":
            return httpx.Response(500)
        return httpx.Response(200, text="not-json")

    client = make_client(handler, allow_write=True)
    try:
        with pytest.raises(FeishuError) as caught:
            await FeishuAPI(client).sheet_append("app", "sheet!A1:A1", [["001"]])
        assert caught.value.outcome == "unknown"
        assert len(requests) == 1
        assert "secret-token-in-transport" not in str(caught.value)
    finally:
        await client.close()


async def test_read_429_retries_bounded(monkeypatch):
    calls = 0
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("feishu_bot.client.asyncio.sleep", sleep)

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "999"}) if calls < 3 else ok({"ok": True})

    client = make_client(handler)
    try:
        assert (await FeishuAPI(client).bot_info())["ok"]
        assert calls == 3 and max(delays) <= 10
    finally:
        await client.close()


async def test_redirect_not_followed_and_error_redaction():
    calls = []
    client = make_client(lambda r: calls.append(r) or httpx.Response(302, headers={"Location": "https://evil.example"}))
    try:
        with pytest.raises(FeishuError):
            await FeishuAPI(client).bot_info()
        assert len(calls) == 1
    finally:
        await client.close()
    client = make_client(lambda r: httpx.Response(200, json={"code": 123, "msg": "invalid test-token"}))
    try:
        with pytest.raises(FeishuError) as caught:
            await FeishuAPI(client).bot_info()
        assert "test-token" not in str(caught.value)
    finally:
        await client.close()


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/path",
        "/open-apis/auth/v3/tenant_access_token/internal",
        "/open-apis/im/v1/chats/../messages",
        "/open-apis/im/v1/chats?forward=1",
    ],
)
async def test_arbitrary_proxy_rejected(path):
    requests = []
    client = make_client(lambda r: requests.append(r) or ok(), allow_write=True)
    try:
        with pytest.raises(ValueError):
            await client.request("GET", path)
        assert not requests
    finally:
        await client.close()


async def test_message_serialization_uuid_and_append_contract():
    requests = []
    client = make_client(lambda r: requests.append(r) or ok({"message_id": "om_sent"}), allow_write=True)
    try:
        api = FeishuAPI(client)
        result = await api.message_send("oc_target", {"text": "中文\n换行"}, request_uuid="batch-001")
        body = json.loads(requests[-1].content)
        assert isinstance(body["content"], str) and json.loads(body["content"])["text"] == "中文\n换行"
        assert body["uuid"] == result["request_uuid"] == "batch-001"
        await api.sheet_append("app", "sheet!A1:B1", [["001", 25]])
        assert requests[-1].url.params["insertDataOption"] == "INSERT_ROWS"
        assert json.loads(requests[-1].content)["valueRange"]["values"][0][0] == "001"
    finally:
        await client.close()


async def test_signed_webhook_without_application_auth(monkeypatch):
    requests = []
    monkeypatch.setattr("feishu_bot.client.time.time", lambda: 1234567890)
    client = FeishuClient(
        Settings(
            allow_write=True,
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
            webhook_secret="sign-secret",
        ),
        transport=httpx.MockTransport(lambda r: requests.append(r) or ok()),
    )
    try:
        await FeishuAPI(client).webhook_send({"text": "test"})
        assert len(requests) == 1 and "Authorization" not in requests[0].headers
        body = json.loads(requests[0].content)
        expected = base64.b64encode(hmac.new(b"1234567890\nsign-secret", b"", hashlib.sha256).digest()).decode()
        assert body["timestamp"] == "1234567890" and body["sign"] == expected
    finally:
        await client.close()


@pytest.mark.parametrize("values", [[[1], [2, 3]], [], [[float("nan")]], [["x" * 50001]], [[]]])
async def test_invalid_cells_no_remote_writes(values):
    requests = []
    client = make_client(lambda r: requests.append(r) or ok(), allow_write=True)
    try:
        with pytest.raises(ValueError):
            await FeishuAPI(client).sheet_write("app", "sheet!A1:B2", values)
        assert not requests
    finally:
        await client.close()


async def test_bitable_write_shapes_and_limits():
    requests = []
    client = make_client(lambda r: requests.append(r) or ok(), allow_write=True)
    try:
        api = FeishuAPI(client)
        await api.bitable_records_create("app", "tbl", [{"fields": {"编码": "001"}}])
        await api.bitable_records_update("app", "tbl", [{"record_id": "rec1", "fields": {"数量": 2}}])
        await api.bitable_records_delete("app", "tbl", ["rec1"])
        assert [r.url.path.rsplit("/", 1)[-1] for r in requests] == ["batch_create", "batch_update", "batch_delete"]
        assert json.loads(requests[-1].content) == {"records": ["rec1"]}
        with pytest.raises(ValueError):
            await api.bitable_records_create("app", "tbl", [{"fields": {}}] * 501)
        with pytest.raises(ValueError):
            await api.bitable_records_update("app", "tbl", [{"record_id": "rec1", "fields": {}}] * 2)
        assert len(requests) == 3
    finally:
        await client.close()


@pytest.mark.parametrize(
    "range,values",
    [("sheet!A1:A1", [[1, 2]]), ("sheet!A1:A1", [[1], [2]]), ("sheet!B1:A2", [[1]]), ("sheet!A5:A1", [[1]])],
)
async def test_target_range_must_fit_values_before_write(range, values):
    requests = []
    client = make_client(lambda r: requests.append(r) or ok(), allow_write=True)
    try:
        with pytest.raises(ValueError):
            await FeishuAPI(client).sheet_write("app", range, values)
        assert not requests
    finally:
        await client.close()
