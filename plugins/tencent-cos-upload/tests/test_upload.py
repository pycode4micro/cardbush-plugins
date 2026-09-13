import asyncio
import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from cos_upload_mcp import config, uploader
from cos_upload_mcp.server import mcp, quiet_sdk, upload_video


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for name in config.NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COS_UPLOAD_READ_USER_ENV", "0")


@pytest.fixture
def configured(monkeypatch):
    for name, value in {"SECRET_ID": "test-id", "SECRET_KEY": "test-key",
                        "BUCKET": "sample-1234567890", "REGION": "ap-guangzhou"}.items():
        monkeypatch.setenv("TENCENT_COS_" + name, value)


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "商品 视频.MP4"
    path.write_bytes(b"synthetic fixture, no real personal media")
    return path


class FakeClient:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append({**kwargs, "bytes": kwargs["Body"].read()})
        return {"ETag": '"etag-test"'}

    def get_presigned_download_url(self, **kwargs):
        self.signing = kwargs
        return "https://sample.example/video.mp4?signature=test-only"


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(uploader, "build_client", lambda settings: fake)
    return fake


def test_upload_unchanged(configured, client, video):
    original = video.read_bytes()
    result = uploader.upload(str(video))
    assert result["status"] == "uploaded"
    assert result["url_type"] == "presigned"
    assert result["expires_seconds"] == 86400
    assert result["expires_at"]
    assert result["access_verified"] is False
    assert client.calls[0]["bytes"] == original == video.read_bytes()
    assert client.calls[0]["ContentLength"] == len(original)
    assert client.calls[0]["Metadata"] == {"x-cos-forbid-overwrite": "true"}
    assert "ACL" not in client.calls[0]
    assert result["etag"] == "etag-test"
    assert video.name not in result["object_key"]
    assert len(list(video.parent.iterdir())) == 1


def test_unique_keys(configured, client, video):
    a = uploader.upload(str(video))
    b = uploader.upload(str(video))
    assert a["object_key"] != b["object_key"]


def test_public_url_no_acl(configured, client, video, monkeypatch):
    monkeypatch.setenv("TENCENT_COS_PUBLIC_BASE_URL", "https://cdn.example.com/assets")
    result = uploader.upload(str(video), prefix="中文 文件/参考", presign=False)
    assert result["url"].startswith("https://cdn.example.com/assets/%E4%B8%AD")
    assert "%20" in result["url"]
    assert result["expires_at"] is None
    assert "ACL" not in client.calls[0]


def test_public_standard_domain(configured, client, video):
    result = uploader.upload(str(video), presign=False)
    assert result["url"].startswith("https://sample-1234567890.cos.ap-guangzhou.myqcloud.com/")


def test_ttl_override(configured, client, video):
    result = uploader.upload(str(video), expires_seconds=600)
    assert result["expires_seconds"] == client.signing["Expired"] == 600


@pytest.mark.parametrize("ttl", [0, -1, 59, 604801, True, "600"])
def test_bad_ttl_no_upload(configured, client, video, ttl):
    with pytest.raises(uploader.UploadError):
        uploader.upload(str(video), expires_seconds=ttl)
    assert not client.calls


@pytest.mark.parametrize("prefix", ["../x", "x/../y", "x//y", "x\n", "x/./y", "字" * 200])
def test_invalid_prefix(prefix):
    # Leading/trailing whitespace is normalized intentionally.
    if prefix == "x\n":
        prefix = "x\ny"
    with pytest.raises(config.ConfigError):
        config.clean_prefix(prefix)


def test_normalized_prefix():
    assert config.clean_prefix(" /folder\\clips/ ") == "folder/clips"


def test_reject_relative(configured, client):
    with pytest.raises(uploader.UploadError):
        uploader.upload("relative.mp4")
    assert not client.calls


def test_reject_directory(configured, client, tmp_path):
    with pytest.raises(uploader.UploadError):
        uploader.upload(str(tmp_path))
    assert not client.calls


@pytest.mark.parametrize("name,data", [("secret.env", b"s"), ("empty.mp4", b"")])
def test_reject_file(configured, client, tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    with pytest.raises(uploader.UploadError):
        uploader.upload(str(path))
    assert not client.calls


def test_missing_file(configured, client, tmp_path):
    result = upload_video(str(tmp_path / "none.mp4"))
    assert result["status"] == "rejected"
    assert not client.calls


def test_file_size_limit(configured, client, video, monkeypatch):
    monkeypatch.setattr(uploader, "MAX_BYTES", 2)
    with pytest.raises(uploader.UploadError):
        uploader.upload(str(video))
    assert not client.calls


def test_root_boundary(configured, client, video, tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("COS_UPLOAD_ALLOWED_ROOT", str(allowed))
    with pytest.raises(uploader.UploadError):
        uploader.upload(str(video))
    monkeypatch.setenv("COS_UPLOAD_ALLOWED_ROOT", str(tmp_path))
    assert uploader.upload(str(video))["status"] == "uploaded"


def test_upload_error_redacted(configured, client, video, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("test-key Authorization https://secret.example/?signature=secret")
    monkeypatch.setattr(client, "put_object", fail)
    result = upload_video(str(video))
    assert result["status"] == "upload_outcome_unknown"
    assert result["object_key"] and result["url"] is None
    assert "test-key" not in json.dumps(result)
    assert "signature=secret" not in json.dumps(result)


def test_sign_error_preserves_uploaded_state(configured, client, video, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("test-key")
    monkeypatch.setattr(client, "get_presigned_download_url", fail)
    result = upload_video(str(video))
    assert result["status"] == "uploaded_url_failed"
    assert len(client.calls) == 1
    assert "test-key" not in json.dumps(result)


def test_source_changes(configured, client, video, monkeypatch):
    original = client.put_object
    def changed(**kwargs):
        response = original(**kwargs)
        video.write_bytes(b"changed data")
        return response
    monkeypatch.setattr(client, "put_object", changed)
    assert uploader.upload(str(video))["status"] == "source_changed"


def test_config_repr_no_secrets(configured):
    assert "test-key" not in repr(config.Settings.load())
    assert "test-id" not in repr(config.Settings.load())


def test_missing_config_handled():
    assert upload_video("file.mp4")["status"] == "rejected"


def test_user_env_dynamic(monkeypatch):
    monkeypatch.setenv("COS_UPLOAD_READ_USER_ENV", "1")
    values = {"TENCENT_COS_SECRET_ID": "first"}
    monkeypatch.setattr(config, "read_user", lambda name: values.get(name))
    assert config.resolve("TENCENT_COS_SECRET_ID") == "first"
    values["TENCENT_COS_SECRET_ID"] = "second"
    assert config.resolve("TENCENT_COS_SECRET_ID") == "second"
    monkeypatch.setenv("TENCENT_COS_SECRET_ID", "")
    assert config.resolve("TENCENT_COS_SECRET_ID") == ""


def test_process_alias_beats_user_primary(monkeypatch):
    monkeypatch.setenv("COS_UPLOAD_READ_USER_ENV", "1")
    monkeypatch.setattr(config, "read_user", lambda name: "registry-value")
    monkeypatch.setenv("COS_SECRET_KEY", "process-alias")
    assert config.resolve("TENCENT_COS_SECRET_KEY") == "process-alias"


def test_user_fallback_disabled(monkeypatch):
    monkeypatch.setattr(config, "read_user", lambda name: pytest.fail("Unexpected registry access"))
    assert config.resolve("TENCENT_COS_SECRET_KEY") == ""


def test_config_name_allowlist():
    with pytest.raises(config.ConfigError):
        config.resolve("UNRELATED_SECRET")


@pytest.mark.parametrize("name,value", [
    ("TENCENT_COS_REGION", "https://evil.example"),
    ("TENCENT_COS_BUCKET", "../../invalid"),
    ("TENCENT_COS_PUBLIC_BASE_URL", "http://example.com"),
    ("TENCENT_COS_PUBLIC_BASE_URL", "https://u:p@example.com"),
    ("TENCENT_COS_PUBLIC_BASE_URL", "https://example.com?key=secret"),
    ("TENCENT_COS_USE_PRESIGNED_URL", "invalid"),
    ("TENCENT_COS_PRESIGNED_EXPIRES_SECONDS", "0"),
    ("COS_UPLOAD_TIMEOUT_SECONDS", "invalid"),
])
def test_bad_config(configured, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(config.ConfigError):
        config.Settings.load()


def test_sdk_header_mapping():
    from qcloud_cos.cos_comm import mapped
    assert mapped({"Metadata": {"x-cos-forbid-overwrite": "true"}}) == {"x-cos-forbid-overwrite": "true"}


def test_real_sdk_signing_offline(configured):
    from urllib.parse import parse_qs, urlsplit
    client = uploader.build_client(config.Settings.load())
    url = client.get_presigned_download_url(Bucket="sample-1234567890", Key="clip.mp4", Expired=600)
    parsed = urlsplit(url)
    assert parsed.scheme == "https"
    assert parsed.hostname == "sample-1234567890.cos.ap-guangzhou.myqcloud.com"
    assert "q-signature" in parse_qs(parsed.query)


def test_https_client_and_token(configured, monkeypatch):
    import qcloud_cos
    captured = {}
    def configuration(**kwargs):
        captured.update(kwargs)
        return kwargs
    monkeypatch.setattr(qcloud_cos, "CosConfig", configuration)
    monkeypatch.setattr(qcloud_cos, "CosS3Client", lambda cfg: cfg)
    uploader.build_client(replace(config.Settings.load(), token="temporary-token"))
    assert captured["Scheme"] == "https"
    assert captured["Token"] == "temporary-token"


def test_four_tools():
    tools = asyncio.run(mcp.list_tools())
    assert [tool.name for tool in tools] == ["upload_video", "download_object", "delete_object", "rename_object"]
    assert set(tools[0].inputSchema["properties"]) == {"file_path", "prefix", "presign", "expires_seconds"}
    assert tools[0].annotations.readOnlyHint is False
    assert tools[0].annotations.openWorldHint is True
    assert tools[2].annotations.destructiveHint is True
    assert tools[3].annotations.destructiveHint is True


def test_sdk_logging_quiet():
    quiet_sdk()
    for name in ("qcloud_cos", "urllib3", "requests"):
        logger = logging.getLogger(name)
        assert not logger.propagate
        assert logger.level > logging.CRITICAL
