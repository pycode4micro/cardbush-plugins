import json

import httpx
import pytest

from feishu_bot.api import FeishuAPI
from feishu_bot.client import FeishuClient
from feishu_bot.routing import AccountRouter, build_plan
from feishu_bot.settings import Settings


def sheet(account="001", sid="sheet1", columns=2):
    return {
        "sheet_id": sid,
        "title": f"店铺({account})",
        "resource_type": "sheet",
        "grid_properties": {"row_count": 1, "column_count": columns},
    }


def record(account="001", name="店铺", value=10):
    return {"__account_id": account, "__account_name": name, "date": "2026-09-16", "value": value}


def plan(sheets, records):
    return build_plan(sheets, records, ["date", "value"], "__account_id", "__account_name", "A", 1)


def test_complete_id_matching_and_column_order():
    result = plan([sheet("0011"), sheet("001", "correct")], [record(), record(value=20)])
    assert len(result) == 1 and result[0]["sheet_id"] == "correct"
    assert result[0]["values"] == [["2026-09-16", 10], ["2026-09-16", 20]]


def test_two_accounts_cannot_share_same_target():
    target = sheet()
    target["title"] = "001(002)"
    with pytest.raises(ValueError, match="同一页签"):
        plan([target], [record("001"), record("002")])


@pytest.mark.parametrize(
    "records",
    [[record(account=123)], [{**record(), "date": ""}], [{k: v for k, v in record().items() if k != "value"}]],
)
def test_invalid_routing_input_rejected(records):
    with pytest.raises((ValueError, TypeError)):
        plan([], records)


async def test_ambiguous_later_group_prevents_all_writes():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, json={"code": 0, "data": {"sheets": [sheet(), sheet("002", "s2"), sheet("002", "s3")]}}
        )

    client = FeishuClient(
        Settings(tenant_access_token="test", allow_write=True), transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ValueError, match="多个页签"):
            await AccountRouter(FeishuAPI(client)).sheet_route_append(
                "app", [record(), record("002")], ["date", "value"]
            )
        assert len(requests) == 1 and requests[0].method == "GET"
    finally:
        await client.close()


async def test_create_extend_append_and_read_only_plan():
    requests = []
    sheets = [sheet(columns=1)]

    def handler(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("sheets/query"):
            data = {"sheets": sheets}
        elif path.endswith("sheets_batch_update"):
            sheets.append(sheet("002", "new_sheet", columns=1))
            data = {"replies": [{"addSheet": {"properties": {"sheetId": "new_sheet"}}}]}
        elif path.endswith("dimension_range"):
            body = json.loads(request.content)["dimension"]
            target = next(s for s in sheets if s["sheet_id"] == body["sheetId"])
            key = "row_count" if body["majorDimension"] == "ROWS" else "column_count"
            target["grid_properties"][key] += body["length"]
            data = {"addCount": body["length"]}
        else:
            assert path.endswith("values_append")
            assert request.url.params["insertDataOption"] == "INSERT_ROWS"
            data = {"updates": {"updatedRows": len(json.loads(request.content)["valueRange"]["values"])}}
        return httpx.Response(200, json={"code": 0, "data": data})

    client = FeishuClient(
        Settings(tenant_access_token="test", allow_write=True), transport=httpx.MockTransport(handler)
    )
    try:
        router = AccountRouter(FeishuAPI(client))
        records = [record(), record(value=12), record("002")]
        preview = await router.sheet_route_plan("app", records, ["date", "value"])
        assert preview["executed"] is False and preview["row_count"] == 3
        assert len(requests) == 1
        result = await router.sheet_route_append("app", records, ["date", "value"])
        assert result["ok"] and len(result["completed"]) == 2
        assert result["completed"][0]["row_count"] == 2
        assert result["completed"][1]["sheet_id"] == "new_sheet"
        dimensions = [json.loads(r.content)["dimension"] for r in requests if r.url.path.endswith("dimension_range")]
        assert [d["majorDimension"] for d in dimensions] == ["COLUMNS", "ROWS", "COLUMNS"]
    finally:
        await client.close()


async def test_partial_failure_reports_committed_and_pending_without_replaying():
    writes = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"code": 0, "data": {"sheets": [sheet("001", "s1"), sheet("002", "s2"), sheet("003", "s3")]}}
            )
        body = json.loads(request.content)
        writes.append(body)
        if len(writes) == 2:
            raise httpx.ReadTimeout("unknown", request=request)
        return httpx.Response(200, json={"code": 0, "data": {"updates": {"updatedRows": 1}}})

    client = FeishuClient(
        Settings(tenant_access_token="test", allow_write=True), transport=httpx.MockTransport(handler)
    )
    try:
        result = await AccountRouter(FeishuAPI(client)).sheet_route_append(
            "app", [record("001"), record("002"), record("003")], ["date", "value"]
        )
        assert not result["ok"]
        assert [x["account_id"] for x in result["completed"]] == ["001"]
        assert result["failed"]["account_id"] == "002"
        assert result["failed"]["stage"] == "append" and result["failed"]["error"]["outcome"] == "unknown"
        assert result["pending_account_ids"] == ["003"] and len(writes) == 2
    finally:
        await client.close()
