from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from .config import Settings, clean_prefix

VIDEO_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".mpeg": "video/mpeg", ".mpg": "video/mpeg", ".ts": "video/mp2t", ".mts": "video/mp2t",
}
MAX_BYTES = 5 * 1024 ** 3


class UploadError(ValueError):
    pass


def build_client(settings: Settings):
    from qcloud_cos import CosConfig, CosS3Client
    return CosS3Client(CosConfig(
        Region=settings.region, SecretId=settings.secret_id, SecretKey=settings.secret_key,
        Token=settings.token or None, Scheme="https", Timeout=settings.timeout,
    ))


def upload(file_path: str, prefix: str | None = None, presign: bool | None = None,
           expires_seconds: int | None = None) -> dict:
    settings = Settings.load()
    raw = Path(file_path).expanduser()
    if not raw.is_absolute():
        raise UploadError("file_path must be an absolute local file path")
    try:
        source = raw.resolve(strict=True)
        if not source.is_file():
            raise UploadError("Select one regular video file, not a directory")
        if settings.allowed_root:
            root = Path(settings.allowed_root).expanduser()
            if not root.is_absolute() or not root.is_dir():
                raise UploadError("COS_UPLOAD_ALLOWED_ROOT must be an existing absolute directory")
            if not source.is_relative_to(root.resolve(strict=True)):
                raise UploadError("The selected file is outside COS_UPLOAD_ALLOWED_ROOT")
        suffix = source.suffix.lower()
        if suffix not in VIDEO_TYPES:
            raise UploadError("Unsupported video extension; use MP4, M4V, MOV, WebM, MKV, AVI, MPEG or TS/MTS")
        before = source.stat()
    except OSError:
        raise UploadError("The selected local file cannot be accessed") from None
    if before.st_size <= 0 or before.st_size > MAX_BYTES:
        raise UploadError("Video must be non-empty and at most 5 GiB")
    ttl = settings.expires_seconds if expires_seconds is None else expires_seconds
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 60 <= ttl <= 604800:
        raise UploadError("expires_seconds must be an integer between 60 and 604800")
    signed = settings.presign if presign is None else presign
    if not isinstance(signed, bool):
        raise UploadError("presign must be a boolean")
    selected_prefix = settings.prefix if prefix is None else clean_prefix(prefix)
    now = datetime.now(timezone.utc)
    # UUID keys avoid filename collisions and do not disclose the local filename.
    key = "/".join(part for part in (selected_prefix, now.strftime("%Y/%m/%d"), uuid4().hex + suffix) if part)
    result = {"bucket": settings.bucket, "region": settings.region, "object_key": key,
              "size_bytes": before.st_size, "content_type": VIDEO_TYPES[suffix]}
    client = build_client(settings)
    try:
        # Streaming single-object upload; no video splitting, decoding or temp files.
        with source.open("rb") as stream:
            response = client.put_object(
                Bucket=settings.bucket, Key=key, Body=stream, ContentType=VIDEO_TYPES[suffix],
                ContentLength=before.st_size, EnableMD5=True,
                Metadata={"x-cos-forbid-overwrite": "true"},
            )
    except Exception:
        return {**result, "status": "upload_outcome_unknown", "url": None,
                "message": "Upload failed or its response was lost. The object may exist at object_key. Check COS before retrying; no automatic full-file retry was started."}
    etag = response.get("ETag") if isinstance(response, dict) else None
    result["etag"] = str(etag).strip('"') if etag else None
    try:
        after = source.stat()
        changed = before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
    except OSError:
        changed = True
    if changed:
        return {**result, "status": "source_changed", "url": None,
                "message": "Object uploaded, but the local file changed or disappeared during upload. Do not use this object without verification."}
    if signed:
        signed_at = datetime.now(timezone.utc)
        try:
            url = str(client.get_presigned_download_url(Bucket=settings.bucket, Key=key, Expired=ttl))
            if not url.startswith("https://"):
                raise ValueError("HTTPS required")
        except Exception:
            return {**result, "status": "uploaded_url_failed", "url": None,
                    "message": "Upload succeeded, but URL signing failed. Keep object_key; do not automatically upload again."}
        expiry = (signed_at + timedelta(seconds=ttl)).isoformat()
    else:
        base = settings.public_base_url or f"https://{settings.bucket}.cos.{settings.region}.myqcloud.com"
        url = base + "/" + quote(key, safe="/-_.~")
        expiry = None
    return {**result, "status": "uploaded", "url": url, "url_type": "presigned" if signed else "public",
            "expires_at": expiry, "expires_seconds": ttl if signed else None,
            "access_verified": False,
            "note": ("Signed GET URL is a bearer credential. Token expiry or bucket policy can shorten access."
                     if signed else "No ACL was changed. This URL works only if existing bucket/CDN policy allows access.")}
