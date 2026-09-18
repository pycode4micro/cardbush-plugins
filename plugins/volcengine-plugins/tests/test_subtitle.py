import asyncio
import hashlib
import json

import httpx
import pytest
from pydantic import ValidationError

from volcengine_plugins import config
from volcengine_plugins.mediakit import MediaKitError
from volcengine_plugins.subtitle import (
    ENDPOINT, SubtitleEraseClient, SubtitleEraseRequest,
    prepare_subtitle_erase, subtitle_erase_capabilities, subtitle_erase_preview,
)

REGION = {"top_left_x": 0.1, "top_left_y": 0.8, "bottom_right_x": 0.9, "bottom_right_y": 0.95}
TASK_ID = "amk-tool-erase-video-subtitle-pro-test"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in config.CONFIG_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ARK_READ_USER_ENV", "0")


def request(**kwargs):
    return SubtitleEraseRequest(video_url="https://source.example/a.mp4?signature=private-source", **kwargs)


def test_default_is_standalone_fine_v5_quality():
    endpoint, body = prepare_subtitle_erase(request())
    assert endpoint == "/api/v1/tools/erase-video-subtitle-pro"
    assert body == {"video_url": request().video_url, "model_version": "v5",
                    "mode": "Subtitle", "output_encode_mode": "Quality"}
    cap = subtitle_erase_capabilities()
    assert cap["limits"]["output_max_resolution"] == "1080p"
    assert cap["provider_default_model_version"] == "v4"
    assert not cap["configured"]


@pytest.mark.parametrize("version", ["v4", "v5"])
def test_exact_native_mapping_and_preview_redacts_private_values(version):
    options = dict(model_version=version, output_encode_mode="Size", erase_ratio_location=[REGION],
                   subtitle_filter={"min_text_height_ratio": 0.01, "max_text_height_ratio": 0.15},
                   time_segment_filter={"mode": "selected", "segments": [{"start_time": 1, "end_time": 3}]},
                   client_token="private-token", callback_args="private-args", queue_id="private-queue",
                   callback_url="https://callback.example/private", media_output_destination="tos://private-bucket")
    _, body = prepare_subtitle_erase(request(**options))
    assert body == {"video_url": request().video_url, "mode": "Subtitle", **options}
    preview = subtitle_erase_preview(request(**options))
    assert preview["paid_request_sent"] is False
    assert "private" not in json.dumps(preview)
    assert any("lower 50%" in item for item in preview["warnings"])
    assert any("do not shorten" in item for item in preview["warnings"])


def test_selected_per_segment_regions_and_empty_default_are_preserved():
    segments = [{"start_time": 0, "end_time": 2, "erase_ratio_location": [REGION]},
                {"start_time": 4, "end_time": 7, "erase_ratio_location": []},
                {"start_time": 9, "end_time": 10}]
    req = request(mode="Text", time_segment_filter={"mode": "selected", "segments": segments})
    _, body = prepare_subtitle_erase(req)
    assert body["time_segment_filter"]["segments"] == segments
    assert "erase_ratio_location" not in body
    assert any("other overlaid text" in item for item in subtitle_erase_preview(req)["warnings"])


@pytest.mark.parametrize("options", [
    {"model_version": "standard"}, {"mode": "Auto"}, {"output_encode_mode": "Lossless"},
    {"resolution": "4k"}, {"fps": 60}, {"mute": True}, {"translate": True},
    {"extra_body": {"mode": "Text"}}, {"api_key": "private"},
    {"erase_ratio_location": []}, {"erase_ratio_location": [REGION] * 21},
    {"erase_ratio_location": [{**REGION, "bottom_right_x": 0.1}]},
    {"erase_ratio_location": [{**REGION, "bottom_right_y": 0.5}]},
    {"erase_ratio_location": [{**REGION, "top_left_x": -0.1}]},
    {"erase_ratio_location": [{**REGION, "bottom_right_x": 1.1}]},
    {"erase_ratio_location": [{**REGION, "top_left_x": float("nan")}]},
    {"erase_ratio_location": [{**REGION, "top_left_x": True}]},
    {"erase_ratio_location": [{**REGION, "top_left_x": "0.1"}]},
    {"time_segment_filter": {"mode": "selected", "segments": []}},
    {"time_segment_filter": {"mode": "skip", "segments": [{"start_time": 1, "end_time": 1}]}},
    {"time_segment_filter": {"mode": "skip", "segments": [{"start_time": -1, "end_time": 2}]}},
    {"time_segment_filter": {"mode": "selected", "segments": [{"start_time": 0, "end_time": float("inf")}]}},
    {"time_segment_filter": {"mode": "skip", "segments": [{"start_time": 0, "end_time": 2, "erase_ratio_location": []}]}},
    {"erase_ratio_location": [REGION], "time_segment_filter": {"mode": "selected", "segments": [{"start_time": 0, "end_time": 2, "erase_ratio_location": [REGION]}]}},
    {"subtitle_filter": {"min_text_height_ratio": 0.2}},
    {"model_version": "v4", "subtitle_filter": {"max_text_height_ratio": 0.001}},
    {"subtitle_filter": {"center_offset_ratio": 1.1}},
    {"mode": "Text", "subtitle_filter": {}},
    {"client_token": "x" * 65}, {"client_token": "中文"}, {"client_token": "x\n"},
    {"callback_args": "中" * 171}, {"callback_url": "https://user:password@example.com"},
    {"media_output_destination": "https://example.com/output"},
])
def test_invalid_or_ignored_options_fail_before_submission(options):
    with pytest.raises(ValidationError):
        request(**options)


@pytest.mark.parametrize("url", ["C:/video.mp4", "data:video/mp4;base64,aaaa", "asset://123", "https://u:p@host/file", "https://host/file#fragment"])
def test_local_or_unsupported_source_is_not_silently_uploaded(url):
    with pytest.raises(ValidationError):
        SubtitleEraseRequest(video_url=url)


@pytest.mark.parametrize("url", ["https://host/file", "http://host/file", "mediakit://id", "vod://space/id", "tos://bucket/key"])
def test_native_source_protocols(url):
    assert SubtitleEraseRequest(video_url=url).video_url == url


def test_ark_key_is_never_used_for_erasure(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "private-ark-key")
    assert not subtitle_erase_capabilities()["configured"]
    calls = []
    client = SubtitleEraseClient(transport=httpx.MockTransport(lambda r: calls.append(r)))
    with pytest.raises(MediaKitError, match="not configured"):
        asyncio.run(client.create(request()))
    assert calls == []


@pytest.mark.parametrize("status", ["running", "completed", "failed"])
def test_submission_and_query_are_separate_and_preserve_native_status(monkeypatch, status):
    monkeypatch.setenv("MEDIAKIT_API_KEY", "test-media-key")
    calls = []
    def handler(r):
        calls.append(r)
        assert r.headers["authorization"] == "Bearer test-media-key"
        if r.method == "POST":
            assert r.url.path == ENDPOINT
            body = json.loads(r.content)
            assert body["client_token"] == "fixed-logical-request"
            assert body["mode"] == "Subtitle" and body["model_version"] == "v5"
            assert set(body) == {"video_url", "model_version", "mode", "output_encode_mode", "client_token"}
            return httpx.Response(200, json={"success": True, "task_id": TASK_ID, "request_id": "request-123"})
        assert r.method == "GET" and r.url.path == "/api/v1/tasks/" + TASK_ID
        result = {"success": True, "task_id": TASK_ID, "status": status}
        if status == "failed":
            result["error"] = {"code": "DownloadFailed", "message": "test-media-key private-source"}
        return httpx.Response(200, json=result)
    async def run():
        client = SubtitleEraseClient(transport=httpx.MockTransport(handler))
        created = await client.create(request(client_token="fixed-logical-request"))
        assert created["paid_request_sent"] is True and created["task"]["request_id"] == "request-123"
        queried = await client.get(created["task"]["task_id"])
        assert queried["task"]["status"] == status and queried["paid_request_sent"] is False
        assert "test-media-key" not in json.dumps(queried)
    asyncio.run(run())
    assert [r.method for r in calls] == ["POST", "GET"]


@pytest.mark.parametrize("failure", ["timeout", "403", "500", "redirect", "badjson", "list", "error_string", "missing_id", "numeric_id"])
def test_no_paid_retries_or_sensitive_error_echo(monkeypatch, failure):
    monkeypatch.setenv("MEDIAKIT_API_KEY", "private-key")
    calls = []
    def handler(r):
        calls.append(r)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-key private-source", request=r)
        if failure == "badjson":
            return httpx.Response(200, text="private-key private-source")
        if failure == "list":
            return httpx.Response(200, json=["private-key"])
        if failure == "error_string":
            return httpx.Response(400, json={"success": False, "error": "private-key"})
        if failure in {"missing_id", "numeric_id"}:
            return httpx.Response(200, json={"success": True, "task_id": None if failure == "missing_id" else 123})
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://other.example/private-key"})
        return httpx.Response(int(failure), json={"success": False, "error": {"code": "AccessDenied", "message": "private-key private-source"}})
    with pytest.raises(MediaKitError) as error:
        asyncio.run(SubtitleEraseClient(transport=httpx.MockTransport(handler)).create(request()))
    assert len(calls) == 1
    assert "private-key" not in str(error.value) and "private-source" not in str(error.value)


def test_download_uses_exact_signed_url_and_no_api_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIAKIT_API_KEY", "private-key")
    signed = "https://cdn.example/result.mp4?sig=a%2Fb%2Bc&x=1&x=2"
    content = b"exact provider video bytes"
    calls = []
    def handler(r):
        calls.append(r)
        assert r.method == "GET"
        return httpx.Response(200, json={"success": True, "status": "completed", "result": {"video_url": signed}})
    def downloader(r):
        assert "authorization" not in r.headers
        assert str(r.url) == signed
        return httpx.Response(200, content=content, headers={"Content-Type": "video/mp4"})
    dest = tmp_path / "clean.mp4"
    result = asyncio.run(SubtitleEraseClient(transport=httpx.MockTransport(handler)).download_task(
        TASK_ID, str(dest), download_transport=httpx.MockTransport(downloader)))
    assert dest.read_bytes() == content
    assert result["sha256"] == hashlib.sha256(content).hexdigest()
    assert result["paid_request_sent"] is False and len(calls) == 1
    with pytest.raises(ValueError, match="no overwrite"):
        asyncio.run(SubtitleEraseClient(transport=httpx.MockTransport(handler)).download_task(TASK_ID, str(dest)))
    assert dest.read_bytes() == content


@pytest.mark.parametrize("status,url", [("running", "https://cdn.example/x"), ("failed", None),
                                        ("completed", "tos://bucket/key"), ("completed", None)])
def test_download_does_not_fetch_unfinished_or_storage_uri(monkeypatch, tmp_path, status, url):
    monkeypatch.setenv("MEDIAKIT_API_KEY", "test-key")
    def handler(r):
        return httpx.Response(200, json={"success": True, "status": status, "result": {"video_url": url}})
    def unexpected(r):
        pytest.fail("Must not try to fetch this result")
    with pytest.raises(MediaKitError):
        asyncio.run(SubtitleEraseClient(transport=httpx.MockTransport(handler)).download_task(
            TASK_ID, str(tmp_path / "out.mp4"), download_transport=httpx.MockTransport(unexpected)))
    assert not (tmp_path / "out.mp4").exists()
