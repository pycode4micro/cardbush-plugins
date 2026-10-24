from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from baiying import client, replay_service as service


ROOT = Path(__file__).resolve().parents[1]
MOBILE = "13800000000"
SCOPE = {"mobile": MOBILE, "account_id": "111", "product_id": "900"}
REASONS = [{"reason": reason, "tips": ""} for reason in client.REQUIRED_RECOMMEND_REASONS]


def video(vid, fragment, *, qualified=True):
    return {"creative_work_id": vid, "related_fragment_id": fragment,
            "play_addr": f"https://v3-dy-o.zjcdn.com/{vid}.mp4", "duration": 34,
            "cover": "https://p3.douyinpic.com/cover.jpg", "clip_status": 0,
            "recommend_reasons": copy.deepcopy(REASONS if qualified else REASONS[:1])}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BAIYING_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(client, "CACHE_PATH", tmp_path / "cookies.sqlite3")
    monkeypatch.setenv("BAIYING_COOKIE_CACHE_PATH", str(tmp_path / "cookies.sqlite3"))
    monkeypatch.setenv("BAIYING_REPLAY_STATE_PATH", str(tmp_path / "state.sqlite3"))


@pytest.fixture
def provider(monkeypatch):
    calls = []
    models = [{
        "promotion_info": {"product_id": "900", "title": "羊绒开衫 ZSU1226", "cover": "product.jpg"},
        "fragment_infos": [{"fragment_id": fid, "duration": 600,
                            "play_addr": f"https://v3-replay.ecombdvod.com/{fid}.mp4"}
                           for fid in ["101", "102", "103", "104"]],
        "auto_cut_videos": [video("1", "101"), video("2", "103", qualified=False),
                            video("1", "101"), {"related_fragment_id": "104", "clip_status": 1}],
    }]
    # Non-empty cookie metadata is enough here; never contact a real provider.
    monkeypatch.setattr(client, "load_login_cookies", lambda *_: [{"name": "test", "value": "private", "domain": "example.test"}])
    monkeypatch.setattr(client, "get_login_subjects", lambda *_: [
        {"account_id": "111", "account_name": "店铺A", "subject_id": "11", "member_id": "12"},
        {"account_id": "222", "account_name": "店铺B", "subject_id": "21", "bus_member_id": "22"},
    ])
    monkeypatch.setattr(client, "switch_subaccount", lambda payload: calls.append(("switch", dict(payload))) or {})

    def fetch(payload):
        calls.append(("fetch", dict(payload)))
        return {"total": len(models), "videos": copy.deepcopy(models)}

    monkeypatch.setattr(client, "fetch_replay_list", fetch)
    monkeypatch.setattr(client, "trigger_auto_clip", lambda _: pytest.fail("Unexpected clipping mutation"))
    return models, calls


def test_queries_scope_account_product_and_separate_existing_from_qualified(provider):
    result = service.search_products({"mobile": MOBILE, "account_id": "111", "query": "zsu1226"})
    assert result["matched_product_count"] == 1
    assert result["products"][0]["qualified_video_count"] == 1
    assert result["products"][0]["existing_video_count"] == 2
    selected = service.get_product_videos(SCOPE)
    assert [v["video_id"] for v in selected["videos"]] == ["1"]
    assert selected["qualified_shortage"] == 4
    assert selected["unclipped_fragment_count"] == 1
    assert selected["clipping_started"] is False
    assert selected["original_fragments"][0]["duration_seconds"] == 600
    assert [v["video_id"] for v in service.get_product_videos({**SCOPE, "quality": "all", "offset": 1})["videos"]] == ["2"]
    for action, payload in provider[1]:
        if action == "fetch":
            assert payload["account_key"] == "111"
        else:
            assert payload["login_subject_uid"] == "11" and payload["user_identity_id"] == "12"


def test_unknown_account_and_wrong_product_are_not_silently_substituted(provider):
    with pytest.raises(ValueError, match="子账号"):
        service.get_product_videos({**SCOPE, "account_id": "333"})
    assert not provider[1]
    with pytest.raises(ValueError, match="该商品"):
        service.get_product_videos({**SCOPE, "product_id": "901"})


def test_empty_original_url_does_not_hide_playable_related_fragment(provider):
    models, _ = provider
    models[0]["fragment_infos"][0]["play_addr"] = ""
    models[0]["auto_cut_videos"][0]["related_fragment_info"] = {
        "fragment_id": "101", "play_addr": "https://v3-replay.ecombdvod.com/original101.mp4"}
    result = service.get_product_videos(SCOPE)
    original = next(f for f in result["original_fragments"] if f["fragment_id"] == "101")
    assert original["download_url"].endswith("/original101.mp4")
    assert original["source"] == "original_fragment"
    assert original["has_clip_record"] is True and original["caption_status"] == "unchecked"
    assert result["original_downloadable_count"] == 4


def test_pagination_limit_is_reported_as_incomplete(provider, monkeypatch):
    monkeypatch.setattr(client, "fetch_replay_list", lambda _: {"total": 100, "videos": provider[0]})
    assert service.get_product_videos({**SCOPE, "max_pages": 1})["scan_complete"] is False
    with pytest.raises(ValueError, match="扫描完整"):
        service.get_product_videos({**SCOPE, "product_id": "901", "max_pages": 1})


def test_prepared_plan_is_bound_and_only_targets_unclipped_fragments(provider, monkeypatch):
    plan = service.prepare_clips(SCOPE)
    assert [f["fragment_id"] for f in plan["selected_fragments"]] == ["102"]
    assert not plan["clipping_started"]
    with pytest.raises(ValueError, match="不属于当前手机号"):
        service.start_clips({"mobile": "13900000000", "plan_id": plan["plan_id"]})
    with pytest.raises(ValueError, match="片段"):
        service.prepare_clips({**SCOPE, "fragment_ids": ["101"]})
    sent = []
    monkeypatch.setattr(client, "trigger_auto_clip", lambda payload: sent.append(payload) or {
        "results": [{"accepted": True, "response": {"code": 0}}],
    })
    first = service.start_clips({"mobile": MOBILE, "plan_id": plan["plan_id"], "account_id": "222", "product_id": "999"})
    assert first["submitted_count"] == 1 and first["generation_complete"] is False
    assert sent[0]["account_key"] == "111" and sent[0]["fragment_ids"] == ["102"]
    assert service.start_clips({"mobile": MOBILE, "plan_id": plan["plan_id"]}) == first
    assert len(sent) == 1


def test_expired_or_changed_plan_does_not_submit(provider):
    plan = service.prepare_clips(SCOPE)
    with service._state() as connection:
        connection.execute("UPDATE clip_plans SET created_at=0 WHERE plan_id=?", (plan["plan_id"],))
    with pytest.raises(ValueError, match="过期"):
        service.start_clips({"mobile": MOBILE, "plan_id": plan["plan_id"]})
    plan = service.prepare_clips(SCOPE)
    provider[0][0]["auto_cut_videos"].append(video("3", "102"))
    with pytest.raises(ValueError, match="已有剪辑"):
        service.start_clips({"mobile": MOBILE, "plan_id": plan["plan_id"]})


@pytest.mark.parametrize("failure", ["rejected", "timeout"])
def test_submission_failure_is_not_reported_as_success_or_retried(provider, monkeypatch, failure):
    plan = service.prepare_clips(SCOPE)
    calls = []

    def submit(payload):
        calls.append(payload)
        if failure == "timeout":
            raise requests.Timeout("response lost")
        return {"results": [{"accepted": False, "response": {"code": 1001, "msg": "账号无权限"}}]}

    monkeypatch.setattr(client, "trigger_auto_clip", submit)
    payload = {"mobile": MOBILE, "plan_id": plan["plan_id"]}
    result = service.start_clips(payload)
    assert result["submitted_count"] == 0
    assert result["unknown_count" if failure == "timeout" else "rejected_count"] == 1
    if failure == "timeout":
        detail = result["results"][0]["error_detail"]
        assert detail["category"] == "timeout" and not detail["retryable"]
        assert detail["next_action"] == "check_clips" and Path(detail["diagnostic_file"]).is_file()
    service.start_clips(payload)
    assert len(calls) == 1


@pytest.mark.parametrize("collection", ["auto_cut_videos", "author_cut_videos"])
def test_check_only_reports_new_videos_from_selected_fragments(provider, collection):
    plan = service.prepare_clips(SCOPE)
    payload = {"mobile": MOBILE, "plan_id": plan["plan_id"]}
    assert service.check_clips(payload)["new_video_count"] == 0
    provider[0][0].setdefault(collection, []).extend([video("3", "102"), video("4", "101")])
    result = service.check_clips(payload)
    assert [v["video_id"] for v in result["new_videos"]] == ["3"]
    assert result["state"] == "new_videos_available"


def test_author_cuts_are_inventory_and_block_duplicate_clipping(provider):
    model = provider[0][0]
    model["author_cut_videos"] = [video("1", "101"), video("3", "102"),
                                 {"related_fragment_id": "105", "clip_status": 1}]
    model["fragment_infos"].append({"fragment_id": "105"})
    result = service.get_product_videos({**SCOPE, "quality": "all"})
    assert result["existing_video_count"] == 3  # Same ID in both lists counts once.
    assert result["qualified_video_count"] == 2
    assert result["unclipped_fragment_count"] == 0
    author_video = next(v for v in result["videos"] if v["video_id"] == "3")
    assert author_video["provider_video_collection"] == "author_cut_videos"
    for fragment_id in ("102", "105"):
        with pytest.raises(ValueError, match="片段"):
            service.prepare_clips({**SCOPE, "fragment_ids": [fragment_id]})


def test_download_checks_every_id_before_writing_and_refreshes_links(provider, tmp_path, monkeypatch):
    downloaded = []
    monkeypatch.setattr(service, "_download", lambda url, target, timeout: downloaded.append(url) or {
        "status": "downloaded", "path": str(target), "bytes": 10,
    })
    with pytest.raises(ValueError, match="不属于"):
        service.download_videos({**SCOPE, "video_ids": ["1", "999"], "output_dir": str(tmp_path / "out")})
    assert not downloaded and not (tmp_path / "out").exists()
    result = service.download_videos({**SCOPE, "video_ids": ["1"], "output_dir": str(tmp_path / "out")})
    assert result["downloaded_count"] == 1
    assert downloaded == ["https://v3-dy-o.zjcdn.com/1.mp4"]
    assert Path(result["manifest_path"]).is_file()
    result = service.download_videos({**SCOPE, "video_ids": ["102"], "source": "original_fragment", "output_dir": str(tmp_path / "out")})
    assert result["downloaded_count"] == 1
    assert downloaded[-1] == "https://v3-replay.ecombdvod.com/102.mp4"


class FakeResponse:
    status_code = 200

    def __init__(self, data=b"\x00\x00\x00\x18ftypisom" + b"data" * 10, content_type="video/mp4"):
        self.data = data
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def close(self):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, **_):
        yield self.data


class FakeSession:
    def __init__(self, response):
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.response = response
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_download_preserves_existing_files_and_removes_partial_data(tmp_path, monkeypatch):
    session = FakeSession(FakeResponse())
    monkeypatch.setattr(service.requests, "Session", lambda: session)
    target = tmp_path / "video.mp4"
    assert service._download("https://v3.zjcdn.com/video", target, 10)["status"] == "downloaded"
    assert target.read_bytes() == session.response.data
    assert "Cookie" not in session.headers and session.calls[0][1]["allow_redirects"] is False
    target.write_bytes(b"user file")
    assert service._download("https://v3.zjcdn.com/video", target, 10)["status"] == "already_exists"
    assert target.read_bytes() == b"user file"
    assert not list(tmp_path.glob("*.part"))
    session.response = FakeResponse(b"<html>expired</html>", "text/html")
    with pytest.raises(ValueError, match="非视频"):
        service._download("https://v3.zjcdn.com/expired", tmp_path / "bad.mp4", 10)
    assert not (tmp_path / "bad.mp4").exists()
    session.response = FakeResponse()
    session.response.headers["Content-Length"] = "1000"
    with pytest.raises(ValueError, match="不完整"):
        service._download("https://v3.zjcdn.com/short", tmp_path / "bad.mp4", 10)
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("url", ["http://v3.zjcdn.com/v", "https://localhost/v", "https://zjcdn.com.evil.test/v", "https://user:password@v3.zjcdn.com/v"])
def test_download_rejects_untrusted_media_targets(url):
    with pytest.raises(ValueError, match="CDN"):
        service._media_url(url)


def test_download_validates_redirect_before_following(tmp_path, monkeypatch):
    response = FakeResponse()
    response.status_code = 302
    response.headers["location"] = "https://127.0.0.1/private"
    session = FakeSession(response)
    monkeypatch.setattr(service.requests, "Session", lambda: session)
    with pytest.raises(ValueError, match="CDN"):
        service._download("https://v3.zjcdn.com/video", tmp_path / "video.mp4", 10)
    assert len(session.calls) == 1
    assert not list(tmp_path.glob("*.mp4")) and not list(tmp_path.glob("*.part"))


def test_client_does_not_convert_provider_error_to_empty_success(monkeypatch):
    class Response(FakeResponse):
        def json(self):
            return {"code": 401, "msg": "登录失效"}

    monkeypatch.setattr(client, "load_login_cookies", lambda *_: [1])
    monkeypatch.setattr(client, "new_session", lambda *_: FakeSession(Response()))
    with pytest.raises(RuntimeError, match="登录失效"):
        client.fetch_replay_list({"mobile": MOBILE})


def test_csrf_handshake_parses_status_not_message_as_token():
    class Session:
        def head(self, *_args, **_kwargs):
            response = FakeResponse()
            response.headers["x-ware-csrf-token"] = self.value
            return response

    session = Session()
    session.value = "0,real-token,60000,,1"
    assert client.fetch_clip_csrf(session, 10) == "real-token"
    session.value = "403,,0,api domain not register or cors rejected,"
    assert client.fetch_clip_csrf(session, 10) == "DOWNGRADE"
    session.value = "403,,0,permission denied,"
    with pytest.raises(RuntimeError, match="校验失败"):
        client.fetch_clip_csrf(session, 10)


@pytest.mark.parametrize("data, valid", [({"total": 0, "smart_video_models": None}, True), ({}, False)])
def test_empty_account_is_distinguished_from_malformed_response(monkeypatch, data, valid):
    class Response(FakeResponse):
        def json(self):
            return {"code": 0, "data": data}

    monkeypatch.setattr(client, "load_login_cookies", lambda *_: [1])
    monkeypatch.setattr(client, "new_session", lambda *_: FakeSession(Response()))
    if valid:
        assert client.fetch_replay_list({"mobile": MOBILE})["videos"] == []
    else:
        with pytest.raises(client.diagnostics.ProviderError) as caught:
            client.fetch_replay_list({"mobile": MOBILE})
        assert caught.value.category == "invalid_response"


@pytest.mark.parametrize("body, accepted", [({"code": 0}, True), ({"code": 403, "msg": "无权限"}, False), ({"code": 0, "success": False}, False)])
def test_client_counts_only_provider_accepted_clips(monkeypatch, body, accepted):
    class Response(FakeResponse):
        def json(self):
            return body

    class Session(FakeSession):
        def post(self, url, **kwargs):
            assert kwargs["json"] == {"fragment_id": "102", "operate_source": 1}
            assert kwargs["allow_redirects"] is False
            return Response()

    monkeypatch.setattr(client, "load_login_cookies", lambda *_: [1])
    monkeypatch.setattr(client, "new_session", lambda *_: Session(Response()))
    result = client.trigger_auto_clip({"mobile": MOBILE, "account_key": "111", "fragment_ids": ["102", "102"], "x_secsdk_csrf_token": "test-token"})
    assert result["triggered_count"] == int(accepted)
    assert len(result["results"]) == 1 and result["results"][0]["accepted"] is accepted


def test_standalone_plugin_handshake_and_missing_login_are_structured(tmp_path):
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "baiying_login_status", "arguments": {"mobile": MOBILE}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "baiying_start_clips", "arguments": {"plan_id": "x", "account_id": "222"}}},
    ]
    result = subprocess.run([sys.executable, str(ROOT / "server.py")],
                            input="\n".join(json.dumps(m) for m in messages) + "\n",
                            text=True, encoding="utf-8", capture_output=True, cwd=tmp_path,
                            env=dict(os.environ), check=True, timeout=20)
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == 4 and not result.stderr
    assert responses[0]["result"]["serverInfo"]["name"] == "baiying-replay"
    tools = {t["name"]: t for t in responses[1]["result"]["tools"]}
    assert len(tools) == 15
    assert tools["baiying_start_clips"]["annotations"]["readOnlyHint"] is False
    assert tools["baiying_prepare_materials"]["annotations"]["readOnlyHint"] is False
    assert tools["baiying_material_status"]["annotations"]["readOnlyHint"] is True
    login = responses[2]["result"]["structuredContent"]
    assert login["needs_sms_code"] is True and login["masked_mobile"] == "138****0000"
    assert "mobile" not in login and "baiying_cookies" not in login
    assert responses[3]["result"]["isError"] is True


def test_unknown_submission_cannot_be_duplicated_by_a_new_plan(provider, monkeypatch):
    sent = []

    def submit(payload):
        sent.append(payload)
        raise requests.Timeout("lost response")

    monkeypatch.setattr(client, "trigger_auto_clip", submit)
    first = service.prepare_clips(SCOPE)
    service.start_clips({**SCOPE, "plan_id": first["plan_id"]})
    second = service.prepare_clips(SCOPE)
    result = service.start_clips({**SCOPE, "plan_id": second["plan_id"]})
    assert len(sent) == 1
    assert result["unknown_count"] == 1
    assert result["results"][0]["existing_plan_id"] == first["plan_id"]


@pytest.mark.parametrize("second_page", ["empty", "repeated"])
def test_inconsistent_catalog_does_not_claim_complete(provider, monkeypatch, second_page):
    monkeypatch.setattr(client, "fetch_replay_list", lambda p: {
        "total": 100, "videos": provider[0] if p["page_no"] == 1 or second_page == "repeated" else []})
    result = service.get_product_videos(SCOPE)
    assert result["scan_complete"] is False
    assert result["replay_records_read"] == 1


def test_transient_provider_failure_retries_the_same_read_page_bounded(provider, monkeypatch):
    calls = []
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    def fetch(payload):
        calls.append(payload["page_no"])
        if len(calls) < 3:
            raise client.ReplayServiceError(500, "下游服务内部异常")
        return {"total": 1, "videos": provider[0]}

    monkeypatch.setattr(client, "fetch_replay_list", fetch)
    assert service.get_product_videos(SCOPE)["matching_video_count"] == 1
    assert calls == [1, 1, 1]


def test_persistent_provider_failure_never_becomes_empty_success(provider, monkeypatch):
    calls = []
    monkeypatch.setattr(service.time, "sleep", lambda _: None)

    def fetch(payload):
        calls.append(payload["page_no"])
        raise client.ReplayServiceError(500, "下游服务内部异常")

    monkeypatch.setattr(client, "fetch_replay_list", fetch)
    with pytest.raises(client.ReplayServiceError):
        service.get_product_videos(SCOPE)
    assert calls == [1, 1, 1]


@pytest.mark.parametrize("failure", [requests.exceptions.SSLError("certificate"), client.ReplayServiceError(429, "请求频繁")])
def test_non_transient_configuration_and_rate_limit_do_not_tight_loop(provider, monkeypatch, failure):
    calls = []

    def fetch(_):
        calls.append(1)
        raise failure

    monkeypatch.setattr(client, "fetch_replay_list", fetch)
    with pytest.raises(type(failure)):
        service.get_product_videos(SCOPE)
    assert calls == [1]
