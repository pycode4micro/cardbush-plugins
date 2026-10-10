import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.material_query import MaterialQuery


def payload():
    return {"advertiser_id": 1, "marketing_goal": "LIVE_PROM_GOODS", "marketing_scene": "FEED",
            "filtering": {"material_type": "VIDEO", "start_time": "2026-10-01", "end_time": "2026-10-10"},
            "fields": ["stat_cost"]}


@pytest.mark.parametrize("key", ["advertiser_id", "marketing_goal", "marketing_scene", "filtering", "fields"])
def test_required_fields_are_rejected_together_before_network(key):
    data = payload(); del data[key]
    with pytest.raises(ValidationError):
        MaterialQuery.model_validate(data)


@pytest.mark.parametrize("change", [
    {"marketing_scene": "AWEME"}, {"fields": []}, {"page_size": 200},
    {"start_time": "2026-10-01"}, {"marketing_goal": "ALL"},
])
def test_invalid_api_shape_fails_locally(change):
    with pytest.raises(ValidationError):
        MaterialQuery.model_validate({**payload(), **change})


@pytest.mark.parametrize("change", [
    {"start_time": "2026-02-30"}, {"end_time": "2025-01-01"},
    {"start_time": "2026-10-01 00:00:00"}, {"material_type": "movie"},
])
def test_filter_contract(change):
    data = payload(); data["filtering"].update(change)
    with pytest.raises(ValidationError): MaterialQuery.model_validate(data)


def test_valid_query_preserves_nested_time_and_only_adds_paging_defaults(monkeypatch):
    import app.mcp_server as server
    data = payload(); original = deepcopy(data); calls = []
    monkeypatch.setattr(server, "_read_call", lambda ctx, fn: fn(SimpleNamespace(get_materials=lambda p: calls.append(p) or {"ok": True})))
    assert server.qianchuan_get_materials(None, data) == {"ok": True}
    assert calls == [{**data, "page": 1, "page_size": 20}]
    assert data == original


def test_published_mcp_schema_explains_contract_and_policy_role():
    from app.mcp_server import mcp
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    schema = tools["qianchuan_get_materials"].inputSchema
    import json
    text = json.dumps(schema)
    for field in ["marketing_goal", "marketing_scene", "material_type", "start_time", "end_time", "fields"]:
        assert field in text
    assert "SHOPPING_MALL" in text
    assert tools["qianchuan_get_materials"].annotations.readOnlyHint
    assert tools["qianchuan_upsert_account_policy"].inputSchema["properties"]["account_role"]["enum"] == ["scale", "test", "retarget", "hold"]
