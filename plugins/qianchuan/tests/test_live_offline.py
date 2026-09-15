import pytest
import httpx

from app.live_delivery import analyze, report_params
from app.launch import _validate_uni_aweme_create_payload
from app.strategy import ValidationError


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Network access forbidden in live integration tests")
    monkeypatch.setattr(httpx.Client, "request", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "request", blocked)


def sample(**changes):
    return {"advertiser_id": "1", "ad_id": "2", "marketing_goal": "LIVE_PROM_GOODS",
            "window_start": "2026-09-01T10:00:00+08:00", "window_end": "2026-09-01T11:00:00+08:00",
            "stat_cost_yuan": "100", "pay_amount_yuan": "1200", "paid_orders": 4,
            "attribution_mature": False, **changes}


def test_offline_roi_is_observation_not_action():
    result = analyze(sample())
    assert result["roi"] == "12"
    assert result["actions"] == []
    assert result["official_state_verified"] is False
    assert result["attribution_mature"] is False


def test_missing_data_is_not_zero():
    assert analyze({})["status"] == "input_required"
    assert analyze(sample(stat_cost_yuan=0))["roi"] is None


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", True])
def test_reject_bad_metrics(value):
    with pytest.raises(ValueError):
        analyze(sample(stat_cost_yuan=value))


def test_do_not_accept_product_snapshot():
    with pytest.raises(ValueError):
        analyze(sample(marketing_goal="VIDEO_PROM_GOODS"))


def test_live_off_and_audit_block_scaling_advice():
    result = analyze(sample(stat_cost_yuan=0, plan_status="LIVE_ROOM_OFF", audit_rejected=True))
    assert len(result["findings"]) >= 3
    assert not result["actions"]


def test_report_is_explicit_live_not_sxt():
    params = report_params(1, "2026-09-01", "2026-09-02", [2, 2])
    assert params["filtering"] == {"ad_ids": [2], "marketing_goal": "LIVE_PROM_GOODS", "order_platform": "QIANCHUAN"}


def test_unverified_live_create_is_blocked():
    with pytest.raises(ValidationError, match="Invalid live UNI payload"):
        _validate_uni_aweme_create_payload({"marketing_goal": "LIVE_PROM_GOODS"})


def test_mcp_report_uses_only_mock(monkeypatch):
    import app.mcp_server as server
    seen = []
    class FakeService:
        def call_tool(self, key, request):
            seen.append((key, request.params))
            return {"code": 0, "request_id": "mock", "data": {"list": [], "page_info": {"page": 1}}}
    monkeypatch.setattr(server, "_strategy_service", lambda ctx: FakeService())
    result = server.qianchuan_get_live_ad_report(None, 1, [2], "2026-09-01", "2026-09-02")
    assert result["request_id"] == "mock"
    assert seen[0][0] == "qianchuan_report_ad_get_v1"
