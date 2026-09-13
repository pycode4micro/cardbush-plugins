import hashlib
import io
from pathlib import Path

import pytest

from cos_upload_mcp import config, objects
from cos_upload_mcp.server import delete_object, download_object, rename_object


class Missing(Exception):
    def get_status_code(self):
        return 404

    def get_error_code(self):
        return "NoSuchKey"


class Body:
    def __init__(self, data):
        self.stream = io.BytesIO(data)

    def get_raw_stream(self):
        return self.stream


class Client:
    def __init__(self):
        self.data = {"clips/source.mp4": b"original-video"}
        self.events = []
        self.versioning = {}
        self.acl = {"CannedACL": "default"}
        self.body = None

    def head_object(self, Bucket, Key):
        self.events.append(("head", Key))
        if Key not in self.data:
            raise Missing()
        return {"Content-Length": str(len(self.data[Key])), "ETag": '"' + hashlib.md5(self.data[Key]).hexdigest() + '"',
                "Last-Modified": "fixed"}

    def get_object(self, **kwargs):
        info = self.head_object(kwargs["Bucket"], kwargs["Key"])
        assert kwargs["IfMatch"] == info["ETag"]
        self.events.append(("get", kwargs["Key"]))
        self.body = Body(self.data[kwargs["Key"]])
        return {**info, "Body": self.body}

    def delete_object(self, **kwargs):
        self.events.append(("delete", kwargs["Key"]))
        assert "VersionId" not in kwargs
        del self.data[kwargs["Key"]]
        return {}

    def copy_object(self, **kwargs):
        source = kwargs["CopySource"]["Key"]
        assert kwargs["CopySourceIfMatch"] == self.head_object(kwargs["Bucket"], source)["ETag"]
        assert kwargs["Metadata"] == {"x-cos-forbid-overwrite": "true"}
        assert kwargs["CopyStatus"] == "Copy"
        assert kwargs["Key"] not in self.data
        self.events.append(("copy", kwargs["Key"]))
        self.data[kwargs["Key"]] = self.data[source]
        return {}

    def get_bucket_versioning(self, **kwargs):
        return self.versioning

    def get_object_acl(self, **kwargs):
        return self.acl

    def get_presigned_download_url(self, **kwargs):
        return "https://example.com/video.mp4?signature=fixture"


@pytest.fixture(autouse=True)
def client(monkeypatch):
    for name in config.NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COS_UPLOAD_READ_USER_ENV", "0")
    for name, value in {"SECRET_ID": "test-id", "SECRET_KEY": "test-key",
                        "BUCKET": "sample-1234567890", "REGION": "ap-guangzhou"}.items():
        monkeypatch.setenv("TENCENT_COS_" + name, value)
    instance = Client()
    monkeypatch.setattr(objects, "build_client", lambda settings: instance)
    return instance


def source_etag(client):
    return client.head_object("sample", "clips/source.mp4")["ETag"].strip('"')


def mutated(client):
    return [event for event in client.events if event[0] in {"delete", "copy"}]


@pytest.mark.parametrize("key", ["", "/", "clips/", "*", "clips/*.mp4", "../source.mp4", "a/../b",
                                "a//b", "https://example.com/x", "a\\b", " x", "x\n", "字" * 400])
def test_invalid_exact_keys(client, key):
    assert delete_object(key)["status"] == "rejected"
    assert rename_object(key, "target.mp4")["status"] == "rejected"
    assert not client.events


def test_delete_preview_then_confirm(client):
    preview = delete_object("clips/source.mp4")
    assert preview["status"] == "confirmation_required"
    assert not mutated(client)
    result = delete_object("clips/source.mp4", True, preview["etag"])
    assert result["status"] == "deleted"
    assert "clips/source.mp4" not in client.data
    assert len(mutated(client)) == 1


def test_delete_requires_etag(client):
    assert delete_object("clips/source.mp4", True)["status"] == "rejected"
    assert not mutated(client)


def test_delete_changed_source(client):
    before = source_etag(client)
    client.data["clips/source.mp4"] = b"new-object"
    assert delete_object("clips/source.mp4", True, before)["status"] == "rejected"
    assert not mutated(client)


def test_delete_missing(client):
    result = delete_object("clips/missing.mp4", True, "old-etag")
    assert result["status"] == "not_found"
    assert not mutated(client)


def test_delete_marker_report(client, monkeypatch):
    monkeypatch.setattr(client, "delete_object", lambda **kwargs: {"x-cos-delete-marker": "true", "x-cos-version-id": "marker-id"})
    result = delete_object("clips/source.mp4", True, source_etag(client))
    assert result["delete_marker"] is True
    assert result["delete_marker_version_id"] == "marker-id"


def test_delete_unknown(client, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("secret-key https://example.com?signature=secret")
    monkeypatch.setattr(client, "delete_object", fail)
    result = delete_object("clips/source.mp4", True, source_etag(client))
    assert result["status"] == "delete_outcome_unknown"
    assert "secret-key" not in str(result)


def test_permission_error_is_not_missing(client, monkeypatch):
    def fail(**kwargs):
        raise PermissionError("secret-key")
    monkeypatch.setattr(client, "head_object", fail)
    result = delete_object("clips/source.mp4", True, "etag")
    assert result["status"] == "rejected"
    assert not mutated(client)


def test_download_success(client, tmp_path):
    target = tmp_path / "商品 视频.mp4"
    result = download_object("clips/source.mp4", str(target))
    assert result["status"] == "downloaded"
    assert target.read_bytes() == client.data["clips/source.mp4"]
    assert result["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert list(tmp_path.iterdir()) == [target]
    assert client.body.stream.closed
    assert not mutated(client)


def test_download_never_overwrites(client, tmp_path):
    target = tmp_path / "exists.mp4"
    target.write_bytes(b"keep")
    assert download_object("clips/source.mp4", str(target))["status"] == "rejected"
    assert target.read_bytes() == b"keep"
    assert not client.events


def test_download_unknown_object(client, tmp_path):
    assert download_object("missing.mp4", str(tmp_path / "out.mp4"))["status"] == "not_found"
    assert not list(tmp_path.iterdir())


def test_download_incomplete_cleanup(client, tmp_path, monkeypatch):
    original = client.get_object
    def incomplete(**kwargs):
        response = original(**kwargs)
        response["Body"] = Body(b"short")
        return response
    monkeypatch.setattr(client, "get_object", incomplete)
    assert download_object("clips/source.mp4", str(tmp_path / "out.mp4"))["status"] == "download_failed"
    assert not list(tmp_path.iterdir())


def test_download_destination_race(client, tmp_path, monkeypatch):
    original = client.get_object
    target = tmp_path / "out.mp4"
    def race(**kwargs):
        target.write_bytes(b"concurrent-writer")
        return original(**kwargs)
    monkeypatch.setattr(client, "get_object", race)
    assert download_object("clips/source.mp4", str(target))["status"] == "download_failed"
    assert target.read_bytes() == b"concurrent-writer"
    assert list(tmp_path.iterdir()) == [target]


def test_download_root(client, tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    monkeypatch.setenv("COS_UPLOAD_ALLOWED_ROOT", str(root))
    assert download_object("clips/source.mp4", str(tmp_path / "no.mp4"))["status"] == "rejected"
    assert not client.events


def test_download_relative_path(client):
    assert download_object("clips/source.mp4", "out.mp4")["status"] == "rejected"


def test_rename_success(client):
    preview = rename_object("clips/source.mp4", "clips/新名称.mp4")
    assert preview["status"] == "confirmation_required"
    assert not mutated(client)
    result = rename_object("clips/source.mp4", "clips/新名称.mp4", True, preview["etag"])
    assert result["status"] == "renamed"
    assert result["source_deleted"] is True
    assert result["atomic"] is False
    assert result["url"].startswith("https://")
    assert "clips/source.mp4" not in client.data
    assert client.data["clips/新名称.mp4"] == b"original-video"
    assert [e[0] for e in mutated(client)] == ["copy", "delete"]


def test_rename_same_key(client):
    assert rename_object("clips/source.mp4", "clips/source.mp4")["status"] == "rejected"
    assert not mutated(client)


def test_rename_existing_target(client):
    client.data["target.mp4"] = b"keep"
    result = rename_object("clips/source.mp4", "target.mp4", True, source_etag(client))
    assert result["status"] == "target_exists"
    assert not mutated(client)


@pytest.mark.parametrize("state", ["Enabled", "Suspended"])
def test_rename_versioning_rejected(client, state):
    client.versioning = {"Status": state}
    result = rename_object("clips/source.mp4", "target.mp4", True, source_etag(client))
    assert result["status"] == "unsupported_versioning"
    assert not mutated(client)


def test_rename_custom_acl_preserved(client):
    client.acl = {"CannedACL": "private"}
    result = rename_object("clips/source.mp4", "target.mp4", True, source_etag(client))
    assert result["status"] == "unsupported_object_acl"
    assert not mutated(client)


def test_rename_copy_failure_no_delete(client, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("test-secret")
    monkeypatch.setattr(client, "copy_object", fail)
    result = rename_object("clips/source.mp4", "target.mp4", True, source_etag(client))
    assert result["status"] == "copy_outcome_unknown"
    assert "test-secret" not in str(result)
    assert "clips/source.mp4" in client.data
    assert not mutated(client)


def test_rename_bad_copy_no_delete(client, monkeypatch):
    original = client.copy_object
    def bad_copy(**kwargs):
        original(**kwargs)
        client.data[kwargs["Key"]] = b"bad-copy"
    monkeypatch.setattr(client, "copy_object", bad_copy)
    result = rename_object("clips/source.mp4", "target.mp4", True, source_etag(client))
    assert result["status"] == "copied_source_retained"
    assert "clips/source.mp4" in client.data
    assert not any(e[0] == "delete" for e in client.events)


def test_rename_changed_source_no_delete(client, monkeypatch):
    original = client.copy_object
    def changed(**kwargs):
        original(**kwargs)
        client.data["clips/source.mp4"] = b"concurrent-change"
    monkeypatch.setattr(client, "copy_object", changed)
    result = rename_object("clips/source.mp4", "target.mp4", True, source_etag(client))
    assert result["status"] == "copied_source_retained"
    assert client.data["clips/source.mp4"] == b"concurrent-change"


def test_rename_delete_failure_retains_copy(client, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("secret")
    monkeypatch.setattr(client, "delete_object", fail)
    result = rename_object("clips/source.mp4", "target.mp4", True, source_etag(client))
    assert result["status"] == "copied_delete_outcome_unknown"
    assert set(client.data) == {"clips/source.mp4", "target.mp4"}


def test_rename_url_failure_is_completed_rename(client, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("secret")
    monkeypatch.setattr(client, "get_presigned_download_url", fail)
    result = rename_object("clips/source.mp4", "target.mp4", True, source_etag(client))
    assert result["status"] == "renamed"
    assert result["url"] is None
    assert set(client.data) == {"target.mp4"}


def test_large_download_and_rename_rejected(client, tmp_path, monkeypatch):
    monkeypatch.setattr(objects, "MAX_BYTES", 1)
    assert download_object("clips/source.mp4", str(tmp_path / "out.mp4"))["status"] == "rejected"
    assert rename_object("clips/source.mp4", "target.mp4")["status"] == "rejected"
    assert not mutated(client)
