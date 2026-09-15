import httpx
import pytest
from app.live_read import LiveReport, diagnose


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("No real network allowed")
    monkeypatch.setattr(httpx.Client, "request", fail)
    monkeypatch.setattr(httpx.AsyncClient, "request", fail)


def query(**changes):
    return dict(advertiser_id=1, data_topic="OVERALL_ROI_LIVE_AWEME",
                dimensions=["mock_dimension"], metrics=["mock_metric"], filters=[],
                start_time="2026-09-01 00:00:00", end_time="2026-09-01 23:59:59",
                order_by=[], **changes)


@pytest.mark.parametrize("field,value", [("data_topic", "SITE_PROMOTION_PRODUCT_AD"),
    ("page", 0), ("metrics", []), ("advertiser_id", True),
    ("end_time", "2026-08-01 00:00:00"), ("end_time", "2026-10-02 00:00:00")])
def test_bad_query(field, value):
    payload = query()
    payload[field] = value
    with pytest.raises(ValueError):
        LiveReport.model_validate(payload)


class Fake:
    def __init__(self, goal="LIVE_PROM_GOODS", ad_id=2, code=0):
        self.goal, self.ad_id, self.code = goal, ad_id, code
        self.calls = []

    def get_uni_promotion_ad_detail(self, params):
        self.calls.append(("detail", params))
        return {"code": self.code, "request_id": "mock-detail", "data": {
            "ad_id": self.ad_id, "marketing_goal": self.goal, "opt_status": "DISABLE"}}

    def get_uni_promotion_ad_suggestions(self, params):
        self.calls.append(("suggestions", params))
        return {"code": 0, "request_id": "mock-suggestion", "data": {"list": []}}


@pytest.mark.parametrize("kwargs", [{"goal": "VIDEO_PROM_GOODS"}, {"ad_id": 3}, {"code": 50000}])
def test_diagnostics_fail_closed(kwargs):
    fake = Fake(**kwargs)
    result = diagnose(fake, 1, 2, 1, 100)
    assert result["status"] == "blocked"
    assert len(fake.calls) == 1
    assert result["actions"] == []


def test_diagnostics_preserve_evidence():
    result = diagnose(Fake(), 1, 2, 1, 100)
    assert result["status"] == "ok"
    assert result["detail"]["data"]["opt_status"] == "DISABLE"
    assert result["suggestions"]["request_id"] == "mock-suggestion"
    assert not result["complete"]
    assert not result["actions"]


def test_mcp_uses_full_domain_only(monkeypatch):
    import app.mcp_server as server
    class Service:
        def get_uni_promotion_report_data(self, params):
            assert params["data_topic"] == "OVERALL_ROI_LIVE_AWEME"
            assert "marketing_goal" not in params
            return {"code": 0, "request_id": "mock", "data": {"rows": [], "page_info": {"total_page": 2}}}
        def get_uni_promotion_report_config(self, params):
            assert len(params["data_topics"]) == 3
            return {"code": 0}
    monkeypatch.setattr(server, "_strategy_service", lambda ctx: Service())
    result = server.qianchuan_get_live_uni_report(None, query())
    assert result["data"]["page_info"]["total_page"] == 2
    assert server.qianchuan_get_live_uni_report_config(None, 1)["code"] == 0
