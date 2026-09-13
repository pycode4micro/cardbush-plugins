"""Exact-object operations. No bucket scans, recursive deletion or automatic retry."""
from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .uploader import MAX_BYTES, build_client


class ObjectError(ValueError):
    pass


def validate_key(key: str) -> str:
    # Never normalize a deletion target: exact key or reject it.
    if (not key or key != key.strip() or key.startswith("/") or key.endswith("/")
            or "://" in key or "\\" in key or any(c in key for c in "*?[]")
            or any(ord(c) < 32 or ord(c) == 127 for c in key)
            or len(key.encode("utf-8")) > 1024
            or any(p in {"", ".", ".."} for p in key.split("/"))):
        raise ObjectError("Use one exact object_key, not a URL, directory, wildcard or relative traversal")
    return key


def not_found(exc: Exception) -> bool:
    try:
        return int(exc.get_status_code()) == 404 and exc.get_error_code() in {"NoSuchKey", "NotFound"}
    except (AttributeError, ValueError, TypeError):
        return False


def head(client, bucket: str, key: str) -> dict | None:
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if not_found(exc):
            return None
        raise ObjectError("Cannot inspect object; check access permissions and network") from None


def etag(info: dict) -> str:
    value = str(info.get("ETag", "")).strip('"')
    if not value:
        raise ObjectError("COS did not return an ETag; operation stopped")
    return value


def size(info: dict) -> int:
    try:
        value = int(info["Content-Length"])
    except (KeyError, ValueError, TypeError):
        raise ObjectError("COS did not return a valid Content-Length") from None
    if value < 0:
        raise ObjectError("Invalid object size")
    return value


def check_confirmation(confirm: bool, expected_etag: str | None) -> None:
    if confirm and (not expected_etag or not expected_etag.strip('"')):
        raise ObjectError("Confirmed changes require expected_etag from a prior preview")


def unchanged(info: dict, expected_etag: str | None) -> None:
    if expected_etag is not None and etag(info) != expected_etag.strip('"'):
        raise ObjectError("Object changed since preview; no destructive request was sent")


def destination_path(file_path: str, settings: Settings) -> Path:
    raw = Path(file_path).expanduser()
    if not raw.is_absolute() or not raw.name or raw.name in {".", ".."}:
        raise ObjectError("file_path must be an absolute destination file path")
    if os.name == "nt" and (":" in raw.name or raw.name.endswith((" ", ".")) or raw.is_reserved()):
        raise ObjectError("Destination is not a normal Windows filename")
    try:
        parent = raw.parent.resolve(strict=True)
        if not parent.is_dir():
            raise ObjectError("Destination parent must be an existing directory")
        destination = parent / raw.name
        if destination.exists() or destination.is_symlink():
            raise ObjectError("Destination already exists; choose a new filename (no overwrite)")
        if settings.allowed_root:
            root = Path(settings.allowed_root).expanduser()
            if not root.is_absolute() or not root.is_dir() or not parent.is_relative_to(root.resolve(strict=True)):
                raise ObjectError("Destination is outside COS_UPLOAD_ALLOWED_ROOT")
        return destination
    except OSError:
        raise ObjectError("Destination directory cannot be accessed") from None


def download(object_key: str, file_path: str) -> dict:
    key = validate_key(object_key)
    settings = Settings.load()
    destination = destination_path(file_path, settings)
    client = build_client(settings)
    info = head(client, settings.bucket, key)
    if info is None:
        return {"status": "not_found", "object_key": key}
    expected_size = size(info)
    expected_etag = etag(info)
    if expected_size > MAX_BYTES:
        raise ObjectError("This tool supports downloads up to 5 GiB")
    temporary = None
    body = None
    completed = False
    received = 0
    digest = hashlib.sha256()
    try:
        response = client.get_object(Bucket=settings.bucket, Key=key, IfMatch='"' + expected_etag + '"')
        body = response["Body"]
        if etag(response) != expected_etag or size(response) != expected_size:
            raise ObjectError("Object changed between inspection and download")
        descriptor, temp_name = tempfile.mkstemp(prefix=".cos-download-", suffix=".part", dir=destination.parent)
        temporary = Path(temp_name)
        with os.fdopen(descriptor, "wb") as output:
            stream = body.get_raw_stream()
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > expected_size:
                    raise ObjectError("Response exceeded the inspected object size")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        if received != expected_size:
            raise ObjectError("Incomplete download; destination was not created")
        # Windows rename is no-clobber; on POSIX link provides atomic no-clobber publication.
        if os.name == "nt":
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
        completed = True
    except Exception:
        return {"status": "download_failed", "object_key": key, "local_path": str(destination),
                "message": "Download failed or destination appeared concurrently. No existing destination was overwritten."}
    finally:
        if body is not None:
            try:
                body.get_raw_stream().close()
            except Exception:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Never delete any path other than the exact temporary file we created.
    return {"status": "downloaded" if completed else "download_failed", "object_key": key,
            "bucket": settings.bucket, "local_path": str(destination), "size_bytes": received,
            "etag": expected_etag, "sha256": digest.hexdigest()}


def delete(object_key: str, confirm: bool = False, expected_etag: str | None = None) -> dict:
    key = validate_key(object_key)
    check_confirmation(confirm, expected_etag)
    settings = Settings.load()
    client = build_client(settings)
    info = head(client, settings.bucket, key)
    if info is None:
        return {"status": "not_found", "object_key": key, "delete_sent": False}
    unchanged(info, expected_etag)
    result = {"object_key": key, "bucket": settings.bucket, "etag": etag(info), "size_bytes": size(info)}
    if not confirm:
        return {**result, "status": "confirmation_required", "delete_sent": False,
                "message": "Delete only after explicit user authorization. Call with confirm=true and this etag as expected_etag. Unversioned deletion may be permanent. Stop concurrent writers to this key."}
    try:
        response = client.delete_object(Bucket=settings.bucket, Key=key)
    except Exception:
        return {**result, "status": "delete_outcome_unknown", "delete_sent": True,
                "message": "Delete response was lost or failed. Inspect COS before retrying; no automatic retry by the plugin."}
    marker = str(response.get("x-cos-delete-marker", "false")).lower() == "true"
    return {**result, "status": "deleted", "delete_sent": True, "delete_marker": marker,
            "delete_marker_version_id": response.get("x-cos-version-id") if marker else None,
            "message": ("A delete marker was created; historical versions were not explicitly deleted."
                        if marker else "Deletion accepted. Recovery depends on bucket versioning/backups; it may be permanent.")}


def rename(object_key: str, new_object_key: str, confirm: bool = False,
           expected_etag: str | None = None) -> dict:
    source, target = validate_key(object_key), validate_key(new_object_key)
    if source == target:
        raise ObjectError("Source and destination object keys must differ")
    check_confirmation(confirm, expected_etag)
    settings = Settings.load()
    client = build_client(settings)
    info = head(client, settings.bucket, source)
    if info is None:
        return {"status": "not_found", "object_key": source}
    unchanged(info, expected_etag)
    result = {"object_key": source, "new_object_key": target, "bucket": settings.bucket,
              "etag": etag(info), "size_bytes": size(info), "atomic": False}
    if head(client, settings.bucket, target) is not None:
        return {**result, "status": "target_exists", "source_deleted": False}
    if size(info) > MAX_BYTES:
        raise ObjectError("Rename supports objects up to 5 GiB; no multipart-copy fallback")
    try:
        versioning = client.get_bucket_versioning(Bucket=settings.bucket)
        acl = client.get_object_acl(Bucket=settings.bucket, Key=source)
    except Exception:
        raise ObjectError("Rename requires GetBucketVersioning and GetObjectACL to check safe-copy conditions") from None
    # Forbid-overwrite is not reliable for enabled/suspended versioning. Do not downgrade silently.
    if versioning.get("Status") not in {None, ""} or info.get("x-cos-version-id"):
        return {**result, "status": "unsupported_versioning", "source_deleted": False,
                "message": "Safe rename is limited to never-versioned buckets. No object was changed."}
    if acl.get("CannedACL") != "default":
        return {**result, "status": "unsupported_object_acl", "source_deleted": False,
                "message": "Rename requires bucket-inherited object ACL. Custom ACL objects are left unchanged to avoid changing access."}
    if not confirm:
        return {**result, "status": "confirmation_required", "source_deleted": False,
                "message": "Non-atomic copy, verify, then delete. Authorize exact source/target, stop concurrent writers, then call confirm=true with this etag as expected_etag. Old URLs stop working after success."}
    try:
        client.copy_object(
            Bucket=settings.bucket, Key=target,
            CopySource={"Bucket": settings.bucket, "Region": settings.region, "Key": source},
            CopyStatus="Copy", CopySourceIfMatch='"' + etag(info) + '"',
            Metadata={"x-cos-forbid-overwrite": "true"},
        )
    except Exception:
        return {**result, "status": "copy_outcome_unknown", "source_deleted": False,
                "message": "Copy failed or response was lost; source deletion was not attempted. Inspect target before retrying."}
    try:
        copied = head(client, settings.bucket, target)
        current = head(client, settings.bucket, source)
        if (copied is None or current is None or size(copied) != size(info)
                or etag(copied) != etag(info) or etag(current) != etag(info)
                or size(current) != size(info)
                or current.get("Last-Modified") != info.get("Last-Modified")):
            raise ObjectError("Copy or source changed")
    except Exception:
        return {**result, "status": "copied_source_retained", "source_deleted": False,
                "message": "Copy/source verification did not pass. Source deletion was not attempted; inspect both keys."}
    try:
        client.delete_object(Bucket=settings.bucket, Key=source)
    except Exception:
        return {**result, "status": "copied_delete_outcome_unknown", "source_deleted": None,
                "message": "Copy verified but source-delete outcome is unknown. Inspect both keys; do not repeat rename blindly."}
    result.update(status="renamed", source_deleted=True)
    try:
        signed_at = datetime.now(timezone.utc)
        url = str(client.get_presigned_download_url(Bucket=settings.bucket, Key=target,
                                                   Expired=settings.expires_seconds))
        if not url.startswith("https://"):
            raise ObjectError("Invalid signed URL")
        result.update(url=url, url_type="presigned", access_verified=False,
                      expires_at=(signed_at + timedelta(seconds=settings.expires_seconds)).isoformat())
    except Exception:
        result.update(url=None, message="Rename completed; URL signing failed. Use new_object_key; do not repeat rename.")
    return result
