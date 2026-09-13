import asyncio
import base64
import io
import json
import wave

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError

from seedream_mcp.client import SeedreamError
from seedream_mcp.video_client import MAX_BODY_BYTES, SeedanceClient, normalize_media, prepare_video, video_preview
from seedream_mcp.video_models import DEFAULT_VIDEO_MODEL, PROFILES, VideoLocalOptions, VideoRequest


def text(s="生成视频，使用音频1的音色。"):
    return {"type": "text", "text": s}


def media(kind="image", role=None, url="asset://asset-example"):
    result = {"type": kind + "_url", kind + "_url": {"url": url}}
    if role is not None:
        result["role"] = role
    elif kind != "image":
        result["role"] = "reference_" + kind
    return result


def prep(model="2.5", content=None, local=None, **kwargs):
    return prepare_video(VideoRequest(model=model, content=content or [text()], **kwargs), local or VideoLocalOptions())


@pytest.mark.parametrize("profile", PROFILES)
def test_exact_model_and_omission(profile):
    body, _, _ = prep(profile, generate_audio=False, watermark=False, priority=0)
    assert body == {"model": PROFILES[profile]["model"], "content": [text()], "generate_audio": False, "watermark": False, "priority": 0}
    for resolution in PROFILES[profile]["resolutions"]:
        assert prep(profile, resolution=resolution)[0]["resolution"] == resolution


@pytest.mark.parametrize("model,resolution", [("2.0-fast", "1080p"), ("2.0-mini", "1080p"), ("2.5", "4k"), ("2.0-mini", "4k"), ("2.0-fast", "4k")])
def test_resolution_rejected_not_downgraded(model, resolution):
    with pytest.raises(ValueError, match="no silent downgrade"):
        prep(model, resolution=resolution)


@pytest.mark.parametrize("model", PROFILES)
def test_duration_boundaries(model):
    maximum = PROFILES[model]["max_duration"]
    for duration in [-1, 4, maximum]:
        assert prep(model, duration=duration)[0]["duration"] == duration
    for duration in [-2, 0, 3, maximum + 1, True, 4.5, "4"]:
        with pytest.raises(ValueError):
            prep(model, duration=duration)


def test_unknown_and_endpoint_profile():
    with pytest.raises(ValueError):
        prep("")
    assert prep("2.5-pro")[0]["model"] == DEFAULT_VIDEO_MODEL
    with pytest.raises(ValueError, match="requires local.capability_profile"):
        prep("ep-actual-id")
    assert prep("ep-actual-id", local=VideoLocalOptions(capability_profile="2.0-mini"))[0]["model"] == "ep-actual-id"
    with pytest.raises(ValueError, match="conflicts"):
        prep("2.0-fast", local=VideoLocalOptions(capability_profile="2.0"))


@pytest.mark.parametrize("model", ["2.0", "2.0-fast", "2.0-mini"])
@pytest.mark.parametrize("field,value", [("output_format", "mp4"), ("output_format", "mov"), ("omni_reference_task_type", "auto"), ("omni_reference_task_type", "edit")])
def test_25_only_fields(model, field, value):
    with pytest.raises(ValueError, match="2.5-only"):
        prep(model, **{field: value})


@pytest.mark.parametrize("field,value", [("seed", 1), ("frames", 96), ("draft", False), ("camera_fixed", False), ("service_tier", "flex")])
def test_unsupported_fields_never_bypass(field, value):
    with pytest.raises(ValidationError):
        prep(**{field: value})
    with pytest.raises(ValueError, match="cannot override"):
        prep(extra_body={field: value}, local=VideoLocalOptions(allow_unverified_parameters=True))


def test_future_fields_optin_and_reserved():
    with pytest.raises(ValueError, match="require"):
        prep(extra_body={"future_option": True})
    assert prep(extra_body={"future_option": True}, local=VideoLocalOptions(allow_unverified_parameters=True))[0]["future_option"] is True
    for key in ["model", "content", "authorization", "headers", "base_url", "resolution", "API_KEY"]:
        with pytest.raises(ValueError, match="cannot override"):
            prep(extra_body={key: "secret"}, local=VideoLocalOptions(allow_unverified_parameters=True))


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("kind,limit_key", [("image", "images"), ("video", "videos"), ("audio", "audios")])
def test_counts(profile, kind, limit_key):
    limit = PROFILES[profile][limit_key]
    refs = [media(kind, "reference_" + kind)] * limit
    if kind == "audio" and profile != "2.5":
        refs = [media("image", "reference_image")] + refs
    prep(profile, content=refs)
    with pytest.raises(ValueError, match="count exceeds"):
        prep(profile, content=refs + [media(kind, "reference_" + kind)])


def test_all_50_25_refs_accepted():
    refs = [media("image", "reference_image")] * 30 + [media("video")] * 10 + [media("audio")] * 10
    body, _, mapping = prep(content=[text()] + refs)
    assert len(body["content"]) == 51
    assert [mapping[29]["label"], mapping[39]["label"], mapping[49]["label"]] == ["图片30", "视频10", "音频10"]


def test_frame_roles_and_omni():
    prep(content=[media()])
    prep(content=[media(role="first_frame"), media(role="last_frame")])
    invalid = [[media(role="last_frame")], [media(), media()], [media(), media(role="last_frame")], [media(), media("audio")], [media(), media("video")], [media(), media(role="reference_image")]]
    for refs in invalid:
        with pytest.raises(ValueError):
            prep(content=refs)
    with pytest.raises(ValueError, match="adaptive"):
        prep(content=[media()], ratio="9:16")
    prep("2.0", content=[media()], ratio="9:16")


@pytest.mark.parametrize("profile", PROFILES)
def test_audio_only(profile):
    if profile == "2.5":
        prep(profile, content=[media("audio")])
    else:
        with pytest.raises(ValueError, match="audio-only"):
            prep(profile, content=[media("audio")])


def test_edit_extend_constraints_and_defaults():
    refs = [media("video")]
    for mode in ["edit", "extend"]:
        body, warnings, _ = prep(content=refs, omni_reference_task_type=mode)
        assert "ratio" not in body and "duration" not in body
        assert any("asynchronously" in w for w in warnings)
        with pytest.raises(ValueError, match="reference_video"):
            prep(content=[media("audio")], omni_reference_task_type=mode)
        with pytest.raises(ValueError, match="adaptive"):
            prep(content=refs, omni_reference_task_type=mode, ratio="9:16")
    with pytest.raises(ValueError, match="duration=-1"):
        prep(content=refs, omni_reference_task_type="edit", duration=8)
    prep(content=refs, omni_reference_task_type="extend", duration=8)
    prep(content=refs, omni_reference_task_type="edit", duration=-1, ratio="adaptive", output_format="mov")
    with pytest.raises(ValueError, match="omni-reference"):
        prep(omni_reference_task_type="auto")


def test_native_order_and_prompt_verbatim():
    content = [media("video"), text("说“幂境”。只用音频1的音色，不借用视频1的台词。"), media("audio"), media("image", "reference_image")]
    body, _, mapping = prep(content=content)
    assert body["content"] == content
    assert [item["label"] for item in mapping] == ["视频1", "音频1", "图片1"]
    assert [item["content_index"] for item in mapping] == [0, 2, 3]


def wav_bytes(seconds):
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * int(seconds * 8000))
    return out.getvalue()


def audio_data(seconds):
    return "data:audio/wav;base64," + base64.b64encode(wav_bytes(seconds)).decode()


def test_local_files_and_inline_validation(tmp_path):
    image = tmp_path / "product.png"
    Image.new("RGB", (400, 600)).save(image)
    audio = tmp_path / "voice.wav"
    audio.write_bytes(wav_bytes(3))
    content = [media("image", "reference_image", str(image)), media("audio", url=str(audio))]
    with pytest.raises(ValueError, match="explicitly enable"):
        prep(content=content)
    body, _, mapping = prep(content=content, local=VideoLocalOptions(allow_local_files=True))
    assert body["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert body["content"][1]["audio_url"]["url"].startswith("data:audio/wav;base64,")
    assert all(item["metadata_checked_locally"] for item in mapping)
    preview = json.dumps(video_preview(body))
    assert str(tmp_path) not in preview and "UklG" not in preview
    Image.new("RGB", (200, 600)).save(image)
    with pytest.raises(ValueError, match="dimensions"):
        prep(content=content, local=VideoLocalOptions(allow_local_files=True))


@pytest.mark.parametrize("seconds", [1, 31])
def test_reference_audio_durations(seconds):
    with pytest.raises(ValueError, match="reference audio must be"):
        prep(content=[media("audio", url=audio_data(seconds))])


def test_reference_audio_total_and_mime():
    with pytest.raises(ValueError, match="Total known"):
        prep(content=[media("audio", url=audio_data(16))] * 2)
    with pytest.raises(ValueError, match="reference audio must be"):
        prep("2.0", content=[media("image", "reference_image"), media("audio", url=audio_data(16))])
    with pytest.raises(ValueError, match="MIME"):
        prep(content=[media("audio", url=audio_data(3).replace("audio/wav", "audio/mp3"))])


@pytest.mark.parametrize("url", ["file:///C:/video.mp4", "C:/video.mp4", "data:video/mp4;base64,AA==", "https://user:secret@example.com/a.mp4", "asset://../foo", "asset-example"])
def test_video_native_urls_only(url):
    with pytest.raises(ValueError):
        prep(content=[media("video", url=url)])


def test_preview_redacts_signed_urls_and_data():
    content = [media("video", url="https://example.com/path?secret=hello"), media("audio", url=audio_data(3))]
    encoded = json.dumps(video_preview(prep(content=content, callback_url="https://example.com/callback?key=hello")[0]))
    assert "hello" not in encoded and "UklG" not in encoded and "example.com" not in encoded


def test_encoded_body_limit(monkeypatch):
    monkeypatch.setattr("seedream_mcp.video_client.MAX_BODY_BYTES", 100)
    with pytest.raises(ValueError, match="64 MiB"):
        prep(content=[text("测" * 100)])


def test_numeric_and_callback_boundaries():
    for kwargs in [{"priority": 10}, {"priority": -1}, {"execution_expires_after": 3599}, {"execution_expires_after": 259201}, {"generate_audio": "true"}, {"safety_identifier": "汉字"}, {"callback_url": "file:///tmp/out"}]:
        with pytest.raises(ValueError):
            prep(**kwargs)


def test_http_native_create_get_no_rewrite_or_retry():
    calls = []
    def handler(req):
        calls.append(req)
        assert req.headers["authorization"] == "Bearer fake-test-key"
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["model"] == DEFAULT_VIDEO_MODEL
            assert body["content"] == [text()]
            assert body["generate_audio"] is False
            assert "ratio" not in body
            return httpx.Response(200, json={"id": "cgt-test"}, headers={"x-tt-logid": "log-123"})
        assert req.url.path == "/api/v3/contents/generations/tasks/cgt-test"
        assert not req.content
        return httpx.Response(200, json={"id": "cgt-test", "status": "succeeded", "content": {"video_url": "https://cdn.example/video.mov", "last_frame_url": "https://cdn.example/frame.jpg"}, "usage": {"total_tokens": 10}, "output_format": "mov"})
    async def run():
        client = SeedanceClient(api_key="fake-test-key", transport=httpx.MockTransport(handler))
        result = await client.create(VideoRequest(content=[text()], generate_audio=False), VideoLocalOptions())
        assert result["task"]["id"] == "cgt-test" and result["request_id"] == "log-123"
        result = await client.get("cgt-test")
        assert result["task"]["content"]["video_url"].endswith(".mov")
        assert result["task"]["usage"]["total_tokens"] == 10
    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 429, 500, 302])
def test_http_failure_safe_single_post(status):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status, json={"error": {"code": "InvalidParameter", "message": "fake-secret signed-url"}}, headers={"x-request-id": "log-123"})
    async def run():
        with pytest.raises(SeedreamError) as e:
            await SeedanceClient(api_key="fake-secret", transport=httpx.MockTransport(handler)).create(VideoRequest(content=[text()]), VideoLocalOptions())
        assert "fake-secret" not in str(e.value)
        assert "InvalidParameter" in str(e.value) and "log-123" in str(e.value)
    asyncio.run(run())
    assert len(calls) == 1


def test_timeout_no_retry():
    calls = []
    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout("fake-secret")
    async def run():
        with pytest.raises(SeedreamError, match="may have been accepted"):
            await SeedanceClient(api_key="fake", transport=httpx.MockTransport(handler)).create(VideoRequest(content=[text()]), VideoLocalOptions())
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [[], {}, {"id": 123}])
def test_malformed_creation_not_success(payload):
    async def run():
        with pytest.raises(SeedreamError):
            await SeedanceClient(api_key="fake", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))).create(VideoRequest(content=[text()]), VideoLocalOptions())
    asyncio.run(run())


@pytest.mark.parametrize("state", ["queued", "running", "failed", "expired", "cancelled"])
def test_task_states_and_error_redaction(state):
    async def run():
        result = await SeedanceClient(api_key="fake", transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": "cgt-test", "status": state, "error": {"code": "InvalidParameter.TaskTypeMismatch", "message": "secret"}}))).get("cgt-test")
        assert result["task"]["status"] == state
        assert result["task"]["error"]["code"] == "InvalidParameter.TaskTypeMismatch"
        assert "secret" not in json.dumps(result)
    asyncio.run(run())


def test_missing_key_and_invalid_task_no_network():
    def handler(_):
        pytest.fail("No request should be sent")
    async def run():
        client = SeedanceClient(api_key="", transport=httpx.MockTransport(handler))
        with pytest.raises(SeedreamError, match="not configured"):
            await client.create(VideoRequest(content=[text()]), VideoLocalOptions())
        with pytest.raises(ValueError, match="Invalid task_id"):
            await client.get("../keys?secret=1")
    asyncio.run(run())
