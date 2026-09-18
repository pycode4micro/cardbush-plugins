import asyncio
import hashlib
import io
import json
import wave
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError

from volcengine_plugins import config
from volcengine_plugins.music import MusicClient, music_capabilities, music_preview, music_request, signed_request
from volcengine_plugins.music_models import BGMRequest, SongRequest
from volcengine_plugins.task_io import TaskError


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for name in config.CONFIG_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(config.USER_ENV_SWITCH, "0")


def client(handler, **kwargs):
    return MusicClient(access_key="test-ak", secret_key="test-sk", session_token="",
                       transport=httpx.MockTransport(handler), **kwargs)


def envelope(result=None, code=0):
    return {"Code": code, "Result": result if result is not None else {"TaskID": "task-123"},
            "ResponseMetadata": {"RequestId": "req-123", "Error": None}}


def test_signing_matches_official_python_reference_vector():
    # Independently produced with the official docs/6369/67269 Python signer,
    # fake AK/SK, fixed UTC date and identical body. No live request was made.
    body = {"Lyrics": "[verse]\n星光落在窗前，风带来你的信。", "ModelVersion": "v5.0", "Duration": 30}
    url, headers, data = signed_request("GenSongForTime", body, "test-ak", "test-sk",
                                       now=datetime(2026, 9, 18, 4, 5, 6, tzinfo=timezone.utc))
    assert url == "https://open.volcengineapi.com/?Action=GenSongForTime&Version=2024-08-12"
    assert headers["x-content-sha256"] == "6d930718923d235429c5875ac08688ebc382f73b00ba2e0aee68d17a618d64c9"
    assert headers["authorization"] == ("HMAC-SHA256 Credential=test-ak/20260918/cn-beijing/imagination/request, "
        "SignedHeaders=content-type;host;x-content-sha256;x-date, "
        "Signature=ef85ef4c6f904ae9abe8015ebfaec427a73f09f0e1e202c7344ba5eedca9122b")
    assert json.loads(data) == body
    assert "星光".encode() in data


def test_session_token_is_signed_and_payload_changes_signature():
    when = datetime(2026, 9, 18, tzinfo=timezone.utc)
    args = ("QuerySong", {"TaskID": "task-123"}, "test-ak", "test-sk")
    _, first, _ = signed_request(*args, now=when)
    _, temporary, _ = signed_request(*args, "test-token", now=when)
    _, changed, _ = signed_request("QuerySong", {"TaskID": "task-124"}, "test-ak", "test-sk", now=when)
    assert temporary["x-security-token"] == "test-token"
    assert "x-date;x-security-token" in temporary["authorization"]
    assert len({first["authorization"], temporary["authorization"], changed["authorization"]}) == 3


def test_song_preview_preserves_lyrics_and_explicit_v5_without_old_style_fields():
    lyrics = "[verse]\n晚风吹过田野\n[chorus]\n星光陪你回家"
    request = SongRequest(Lyrics=lyrics, Prompt="温暖的民谣，吉他伴奏，柔和女声。", Duration=180,
                          CallbackURL="https://callback.example/private?token=secret", TosBucket="private-bucket")
    result = music_preview(request)
    assert result["body"]["Lyrics"] == lyrics
    assert result["body"]["Prompt"] == request.Prompt
    assert result["body"]["ModelVersion"] == "v5.0"
    assert result["body"]["SkipCopyCheck"] is False
    assert result["query"]["Action"] == "GenSongForTime"
    assert result["billing_reference"]["reference_amount"] == 0.36
    assert result["billing_reference"]["confirmed_v5_price"] is False
    assert "private" not in json.dumps(result)
    assert result["paid_request_sent"] is False


@pytest.mark.parametrize("field", ["Genre", "Mood", "Gender", "Timbre", "Instrument", "Tempo"])
def test_song_rejects_ignored_legacy_fields(field):
    with pytest.raises(TaskError) as error:
        music_request({"Prompt": "温暖的民谣歌曲", field: "private-text"}, "song")
    assert error.value.details["paid_request_sent"] is False
    assert "private-text" not in str(error.value)


@pytest.mark.parametrize("value", ["v4.0", "v4.3", "v6.0", "latest"])
def test_no_other_model_or_alias_can_silently_change_v5(value):
    with pytest.raises(ValidationError):
        SongRequest(Prompt="温暖的民谣歌曲", ModelVersion=value)
    with pytest.raises(ValidationError):
        BGMRequest(Text="温暖的背景音乐", Version=value)


@pytest.mark.parametrize("fields", [{}, {"Lyrics": "    "}, {"Lyrics": "短歌词"},
    {"Prompt": "x" * 2001}, {"Lyrics": "中" * 701}, {"Lyrics": "a" * 2001, "Lang": "English"},
    {"Prompt": "合法歌曲描述", "Duration": 29}, {"Prompt": "合法歌曲描述", "Duration": 241},
    {"Prompt": "合法歌曲描述", "Duration": "30"}, {"Prompt": "合法歌曲描述", "Duration": True},
    {"Prompt": "合法歌曲描述", "Lang": "French"}, {"Prompt": "合法歌曲描述", "CallbackURL": "https://u:p@x.example"}])
def test_invalid_song_requests_are_preflight_failures(fields):
    with pytest.raises(TaskError) as error:
        music_request(fields, "song")
    assert error.value.details["stage"] == "preflight"


def test_english_and_cantonese_and_optional_duration():
    assert SongRequest(Lyrics="a" * 2000, Lang="English").Lang == "English"
    assert SongRequest(Lyrics="月光照住回家路", Lang="Cantonese", Duration=240).Duration == 240
    assert music_preview(SongRequest(Prompt="温暖的民谣歌曲"))["billing_reference"]["reference_amount"] is None


@pytest.mark.parametrize("fields", [{"Text": "English only"}, {"Text": ""}, {"Text": "钢琴伴奏", "Duration": 29},
    {"Text": "钢琴伴奏", "Duration": 121}, {"Text": "钢琴伴奏", "Segments": []},
    {"Text": "钢琴伴奏", "Segments": [{"Name": "intro", "Duration": 5}]},
    {"Text": "钢琴伴奏", "Segments": [{"Name": "intro", "Duration": 4}, {"Name": "outro", "Duration": 30}]},
    {"Text": "钢琴伴奏", "Segments": [{"Name": "verse", "Duration": 70}, {"Name": "outro", "Duration": 60}]},
    {"Text": "钢琴伴奏", "Genre": "Pop"}, {"Text": "钢琴伴奏", "VodFormat": "mp3"}])
def test_bgm_native_limits_and_unsupported_fields(fields):
    with pytest.raises(TaskError):
        music_request(fields, "bgm")


def test_bgm_v5_segments_and_duration_precedence():
    request = BGMRequest(Text="温暖钢琴与吉他，80秒", Duration=90,
                         Segments=[{"Name": "intro", "Duration": 10}, {"Name": "chorus", "Duration": 30}])
    preview = music_preview(request)
    assert preview["body"]["Version"] == "v5.0"
    assert preview["query"]["Version"] == "2024-08-12"
    assert preview["body"]["Duration"] == 90
    assert preview["billing_reference"]["duration_basis_seconds"] == 40
    assert preview["body"]["EnableInputRewrite"] is False
    assert BGMRequest(Text="温暖钢琴与吉他", Duration=120).Duration == 120


@pytest.mark.parametrize("kind,billing,action", [("song", "prepaid", "GenSongV4"),
    ("song", "postpaid", "GenSongForTime"), ("bgm", "prepaid", "GenBGM"), ("bgm", "postpaid", "GenBGMForTime")])
def test_exact_native_action_and_signed_body(kind, billing, action):
    seen = []
    def handler(req):
        seen.append(req)
        assert req.method == "POST"
        assert req.url.host == "open.volcengineapi.com"
        assert dict(req.url.params) == {"Action": action, "Version": "2024-08-12"}
        assert req.headers["x-content-sha256"] == hashlib.sha256(req.content).hexdigest()
        body = json.loads(req.content)
        assert body["ModelVersion" if kind == "song" else "Version"] == "v5.0"
        assert "authorization" in req.headers and "Bearer" not in req.headers["authorization"]
        return httpx.Response(200, json=envelope())
    request = SongRequest(Lyrics="[verse]\n星光陪你走过长夜") if kind == "song" else BGMRequest(Text="温暖的钢琴背景音乐")
    result = asyncio.run(client(handler).create(request, billing))
    assert len(seen) == 1 and result["task"]["id"] == "task-123"
    assert result["model_version"] == "v5.0" and result["billing"]["charged"] is None


def test_missing_music_credentials_do_not_use_ark_or_mediakit(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "other-secret")
    monkeypatch.setenv("MEDIAKIT_API_KEY", "other-secret")
    instance = MusicClient(transport=httpx.MockTransport(lambda req: pytest.fail("No HTTP allowed")))
    with pytest.raises(TaskError) as error:
        asyncio.run(instance.create(SongRequest(Prompt="关于星空的歌曲")))
    assert error.value.details["paid_request_sent"] is False
    assert not music_capabilities()["configured"]
    assert "other-secret" not in json.dumps(music_capabilities())


@pytest.mark.parametrize("timeout", ["nan", "inf", "0", "301", "bad"])
def test_bad_timeout_is_free_preflight_error(timeout):
    instance = client(lambda req: pytest.fail("No HTTP allowed"), timeout=timeout)
    with pytest.raises(TaskError) as error:
        asyncio.run(instance.create(SongRequest(Prompt="关于星空的歌曲")))
    assert error.value.details["stage"] == "preflight"


def test_bad_billing_mode_never_falls_back():
    with pytest.raises(TaskError):
        asyncio.run(client(lambda req: pytest.fail("No HTTP allowed")).create(SongRequest(Prompt="关于星空的歌曲"), "auto"))


@pytest.mark.parametrize("payload,http_status", [(envelope(code=200028), 200),
    ({"Code": 0, "Result": {}}, 200), ({"Code": False, "Result": {"TaskID": "task-123"}}, 200),
    ({"Code": 0, "Result": {"TaskID": "task-123"}, "ResponseMetadata": {"Error": {"Message": "private-secret"}}}, 200),
    (envelope(), 302), (envelope(code=400040), 429), (["private-secret"], 500)])
def test_provider_failures_are_uncertain_and_never_retried(payload, http_status):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(http_status, json=payload, headers={"location": "https://other.example/"})
    with pytest.raises(TaskError) as error:
        asyncio.run(client(handler).create(SongRequest(Prompt="关于星空的歌曲")))
    assert len(seen) == 1
    assert error.value.details["paid_request_sent"] is True
    assert error.value.details["billing"]["charged"] is None
    assert "private-secret" not in str(error.value)


def test_timeout_does_not_repeat_generation_or_expose_headers():
    seen = []
    def handler(req):
        seen.append(req)
        raise httpx.ReadTimeout("private-secret test-sk authorization", request=req)
    with pytest.raises(TaskError) as error:
        asyncio.run(client(handler).create(SongRequest(Prompt="关于星空的歌曲")))
    assert len(seen) == 1 and error.value.details["paid_request_sent"] is True
    assert "test-sk" not in str(error.value) and "private-secret" not in str(error.value)


@pytest.mark.parametrize("native,status", [(0, "queued"), (1, "running"), (2, "succeeded"), (3, "failed"),
                                         (7, "unknown"), (True, "unknown"), ("2", "unknown")])
def test_query_normalizes_only_documented_states(native, status):
    def handler(req):
        assert req.url.params["Action"] == "QuerySong"
        assert json.loads(req.content) == {"TaskID": "task-123"}
        return httpx.Response(200, json=envelope({"TaskID": "task-123", "Status": native,
            "Progress": 100, "FailureReason": {"Code": 300061, "Msg": "private-secret"}}))
    result = asyncio.run(client(handler).get("task-123"))
    assert result["task"]["status"] == status and result["paid_request_sent"] is False
    assert "private-secret" not in json.dumps(result)


def test_query_preserves_native_lyrics_captions_and_style_info():
    detail = {"Lyrics": "[verse]\n星光陪你回家", "Captions": '{"test_timestamp":123}',
              "StyleInfo": '{"duration":30}', "Duration": 30.25, "AudioUrl": "https://media.example/a.wav?sig=x"}
    result = asyncio.run(client(lambda req: httpx.Response(200, json=envelope({
        "TaskID": "task-123", "Status": 2, "SongDetail": detail}))).get("task-123"))
    assert result["task"]["song"]["lyrics"] == detail["Lyrics"]
    assert result["task"]["song"]["captions"] == detail["Captions"]
    assert result["task"]["song"]["style_info"] == detail["StyleInfo"]
    assert result["task"]["song"]["duration_seconds"] == 30.25


def test_query_rejects_mismatched_task_and_missing_result():
    with pytest.raises(TaskError) as error:
        asyncio.run(client(lambda req: httpx.Response(200, json=envelope({"TaskID": "other", "Status": 2}))).get("task-123"))
    assert error.value.details["paid_request_sent"] is False


def audio_bytes():
    out = io.BytesIO()
    with wave.open(out, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 800)
    return out.getvalue()


def result_client(status=2):
    return client(lambda req: httpx.Response(200, json=envelope({"TaskID": "task-123", "Status": status,
        "SongDetail": {"AudioUrl": "https://media.example/a.wav?a=%2F&b=1+2&sig=private",
                       "Lyrics": "星光陪你回家", "Captions": "{}", "StyleInfo": '{"duration":30}', "Duration": 30}})))


def test_download_preserves_signed_url_bytes_and_lyrics_sidecar(tmp_path):
    raw = audio_bytes()
    seen = []
    def handler(req):
        seen.append(req)
        assert req.url.raw_path == b"/a.wav?a=%2F&b=1+2&sig=private"
        assert "authorization" not in req.headers and "x-security-token" not in req.headers
        return httpx.Response(200, content=raw, headers={"content-type": "audio/wav"})
    target, metadata = tmp_path / "song.wav", tmp_path / "song.json"
    saved = asyncio.run(result_client().download_task("task-123", str(target), metadata_dest=str(metadata),
                           download_transport=httpx.MockTransport(handler)))
    assert target.read_bytes() == raw
    assert saved["sha256"] == hashlib.sha256(raw).hexdigest()
    assert saved["audio"]["detected_format"] == "wav" and saved["audio"]["header_readable"]
    assert saved["audio"]["playback_qc_performed"] is False
    sidecar = json.loads(metadata.read_text(encoding="utf-8"))
    assert sidecar["lyrics"] == "星光陪你回家" and sidecar["captions"] == "{}"
    assert "private" not in metadata.read_text(encoding="utf-8")
    assert len(seen) == 1 and not saved["warnings"]


def test_actual_format_mismatch_is_reported_without_conversion(tmp_path):
    raw = audio_bytes()
    target = tmp_path / "song.mp3"
    saved = asyncio.run(result_client().download_task("task-123", str(target),
        download_transport=httpx.MockTransport(lambda req: httpx.Response(200, content=raw))))
    assert saved["audio"]["detected_format"] == "wav" and saved["warnings"]
    assert target.read_bytes() == raw


@pytest.mark.parametrize("status", [0, 1, 3, 4])
def test_non_success_tasks_are_never_downloaded(tmp_path, status):
    with pytest.raises(TaskError):
        asyncio.run(result_client(status).download_task("task-123", str(tmp_path / "x.wav"),
            download_transport=httpx.MockTransport(lambda req: pytest.fail("No CDN request"))))
    assert not list(tmp_path.iterdir())


def test_existing_outputs_and_colliding_paths_are_rejected_before_network(tmp_path):
    existing = tmp_path / "existing.json"
    existing.write_text("untouched")
    instance = client(lambda req: pytest.fail("No HTTP allowed"))
    for dest, metadata in [(str(existing), None), (str(tmp_path / "a.wav"), str(existing)),
                           (str(tmp_path / "a.wav"), str(tmp_path / "a.wav")), ("relative.wav", None)]:
        with pytest.raises(TaskError):
            asyncio.run(instance.download_task("task-123", dest, metadata_dest=metadata))
    assert existing.read_text() == "untouched" and len(list(tmp_path.iterdir())) == 1


def test_oversize_download_does_not_publish_partial_output(tmp_path):
    target = tmp_path / "a.wav"
    with pytest.raises(TaskError):
        asyncio.run(result_client().download_task("task-123", str(target), max_bytes=10,
            download_transport=httpx.MockTransport(lambda req: httpx.Response(200, content=audio_bytes()))))
    assert not list(tmp_path.iterdir())


def test_metadata_write_failure_reports_saved_audio_without_regeneration(tmp_path, monkeypatch):
    import volcengine_plugins.music as music
    monkeypatch.setattr(music, "_write_metadata", lambda *args: (_ for _ in ()).throw(FileExistsError()))
    target = tmp_path / "a.wav"
    saved = asyncio.run(result_client().download_task("task-123", str(target), metadata_dest=str(tmp_path / "a.json"),
        download_transport=httpx.MockTransport(lambda req: httpx.Response(200, content=audio_bytes()))))
    assert target.is_file() and saved["metadata_path"] is None and saved["warnings"]
