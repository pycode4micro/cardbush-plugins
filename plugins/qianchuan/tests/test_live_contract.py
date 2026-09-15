import pytest
import httpx
from types import SimpleNamespace
from pydantic import ValidationError
from app.live_contract import validate_live


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Real network forbidden")
    monkeypatch.setattr(httpx.Client, "request", fail)
    monkeypatch.setattr(httpx.AsyncClient, "request", fail)


def payload():
    return {"advertiser_id": 1, "aweme_id": 2, "name": "offline-live-test",
            "marketing_goal": "LIVE_PROM_GOODS", "creative_setting": {"smart_select_material": True},
            "delivery_setting": {"budget": 300, "smart_bid_type": "SMART_BID_CUSTOM", "roi2_goal": 12,
                                 "live_schedule_type": "SCHEDULE_FROM_NOW"}}


def test_live_does_not_require_product_or_video_schedule():
    assert validate_live(payload())["marketing_goal"] == "LIVE_PROM_GOODS"


@pytest.mark.parametrize("field", ["product_ids", "multi_product_creative_list", "campaign_payload", "marketing_scene"])
def test_product_fields_not_accepted(field):
    p = payload()
    p[field] = []
    with pytest.raises(ValidationError):
        validate_live(p)


def test_bid_mode_conflict():
    p = payload()
    p["delivery_setting"]["smart_bid_type"] = "SMART_BID_CONSERVATIVE"
    with pytest.raises(ValidationError):
        validate_live(p)
    del p["delivery_setting"]["roi2_goal"]
    assert validate_live(p)


def test_gate_blocks_before_any_service_access(monkeypatch):
    import app.mcp_server as server
    monkeypatch.setattr(server, "_settings", lambda ctx: SimpleNamespace(qianchuan_live_create_enabled=False))
    result = server.qianchuan_create_live_uni_ad(None, payload(), True, "offline mock test")
    assert result == {"status": "blocked", "reason": "live_create_disabled", "writes_submitted": False}


def test_explicit_enabled_path_delegates_to_guarded_tool(monkeypatch):
    import app.mcp_server as server
    monkeypatch.setattr(server, "_settings", lambda ctx: SimpleNamespace(qianchuan_live_create_enabled=True))
    seen = []
    def fake(ctx, payload, confirm, reason):
        seen.append(payload)
        return {"code": 0, "data": {"ad_id": 3}, "request_id": "mock-only"}
    monkeypatch.setattr(server, "qianchuan_create_uni_aweme_ad", fake)
    assert server.qianchuan_create_live_uni_ad(None, payload(), True, "offline mock test")["data"]["ad_id"] == 3
    assert seen[0]["marketing_goal"] == "LIVE_PROM_GOODS"
