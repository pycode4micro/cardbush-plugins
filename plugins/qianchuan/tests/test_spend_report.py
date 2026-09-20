from decimal import Decimal
from types import SimpleNamespace
import pytest
import httpx
from app import spend_report as report
from app.qianchuan_client import QianchuanClient, QianchuanConfig, QianchuanApiError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*a, **k):
        pytest.fail("Live network forbidden")
    monkeypatch.setattr(httpx.Client, "request", fail)
    monkeypatch.setattr(httpx.AsyncClient, "request", fail)


class Fake:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []
    def get(self, path, **kwargs):
        self.calls.append((path, kwargs["params"]))
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return {"code": 0, "data": {"stat_cost_for_roi2": value}, "request_id": "mock"}


def test_money_no_duplicate_account_or_dimension():
    client = Fake(["320.82", "460.74"])
    row = report.collect_account(client, "secret", {"advertiser_id": "1"}, "2026-09-18", "2026-09-18", "ALL")
    result = report.summarize([row], 1)
    assert result["total_cost_yuan"] == "781.56"
    assert result["complete"]
    assert [p[1]["adlab_scene"] for p in client.calls] == list(report.SCENES)
    assert all(p[1]["fields"] == ["stat_cost_for_roi2"] for p in client.calls)
    assert "secret" not in str(result)


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "-1", "bad",
    QianchuanApiError("rate", code=40110, request_id="failed-request")])
def test_missing_or_failed_is_not_zero(value):
    row = report.collect_account(Fake(["10.10", value]), "secret", {"advertiser_id": "1"}, "2026-09-18", "2026-09-18", "ALL")
    result = report.summarize([row], 1)
    assert result["total_cost_yuan"] is None
    assert result["known_cost_yuan"] == "10.10"
    assert not result["complete"]


def test_unfinished_scope_is_partial():
    assert not report.summarize([], 51)["complete"]
    assert report.summarize([], 51)["total_cost_yuan"] is None


def test_job_deduplicates_accounts_and_active_job(monkeypatch):
    report._jobs.clear()
    tasks = []
    monkeypatch.setattr(report, "_pool", SimpleNamespace(submit=lambda fn: tasks.append(fn)))
    accounts = [{"advertiser_id": "1"}, {"advertiser_id": "1"}]
    client = Fake(["0", "0"])
    started = report.start_report(client, "secret", accounts, "2026-09-18", "2026-09-18")
    again = report.start_report(client, "secret", accounts, "2026-09-18", "2026-09-18")
    assert again["job_id"] == started["job_id"]
    assert started["account_count"] == 1
    tasks[0]()
    assert report.result(started["job_id"])["status"] == "complete"
    assert report.result("missing")["status"] == "not_found"


def test_read_retries_but_write_does_not(monkeypatch):
    import app.qianchuan_client as module
    monkeypatch.setattr(module, "_pace_read", lambda *a: None)
    monkeypatch.setattr(module.time, "sleep", lambda *a: None)
    client = QianchuanClient(QianchuanConfig("https://example.invalid"))
    calls = []
    def send(*a, **k):
        calls.append(a[0])
        raise QianchuanApiError("rate limit", code=40110)
    monkeypatch.setattr(client, "_request", send)
    with pytest.raises(QianchuanApiError):
        client.get("/report", access_token="secret")
    assert calls == ["GET"] * 3
    calls.clear()
    with pytest.raises(QianchuanApiError):
        client.post("/update", access_token="secret")
    assert calls == ["POST"]


def test_parameter_error_no_retry(monkeypatch):
    import app.qianchuan_client as module
    monkeypatch.setattr(module, "_pace_read", lambda *a: None)
    client = QianchuanClient(QianchuanConfig("https://example.invalid"))
    calls = []
    def send(*a, **k):
        calls.append(1)
        raise QianchuanApiError("bad params", code=40000)
    monkeypatch.setattr(client, "_request", send)
    with pytest.raises(QianchuanApiError):
        client.get("/report", access_token="secret")
    assert len(calls) == 1


def test_explicit_unresolved_account_rejected(monkeypatch):
    from app import mcp_server as server
    settings = SimpleNamespace(oauth_gateway_base_url="https://example.invalid", oauth_gateway_hmac_secret="secret",
                               qianchuan_allowed_advertiser_ids=[], qianchuan_allow_all_authorized_advertisers=True)
    monkeypatch.setattr(server, "_settings", lambda ctx: settings)
    monkeypatch.setattr(server, "OAuthGatewayClient", lambda s: SimpleNamespace(get_access_token_response=lambda: {
        "access_token": "secret", "advertisers": [{"advertiser_id": "1", "status": "active", "account_role": "QIANCHUAN"}]}))
    with pytest.raises(server.ToolError):
        server.qianchuan_start_spend_report(None, "2026-09-18", "2026-09-18", ["2"])


def test_route_guide_is_offline():
    assert report.guide()["default_for_yesterday_or_account_spend"] == "qianchuan_start_spend_report"


@pytest.mark.parametrize("start,end", [("2026-09-19", "2026-09-18"), ("2026-01-01", "2026-03-01"), ("bad", "bad")])
def test_bad_dates(start, end):
    with pytest.raises(ValueError):
        report.validate_dates(start, end)
