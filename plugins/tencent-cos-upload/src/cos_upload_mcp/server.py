from __future__ import annotations

import logging
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .config import ConfigError
from .branding import plugin_icons
from .uploader import UploadError, upload
from .objects import ObjectError, download, delete, rename


def quiet_sdk() -> None:
    # COS debug messages can include authorization headers or signed URLs.
    for name in ("qcloud_cos", "urllib3", "requests"):
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        logger.setLevel(logging.CRITICAL + 1)


quiet_sdk()
mcp = FastMCP("tencent-cos-upload", icons=plugin_icons(), log_level="ERROR", instructions=(
    "Manage only exact files/objects explicitly selected by the user in their configured Tencent COS bucket. "
    "Tools: upload_video, download_object, delete_object, rename_object. "
    "Delete/rename require an explicit user request, a preview, and confirm=true with the preview ETag. "
    "A preview is not user consent. Never scan or recursively delete a prefix. "
    "Never cut, transcode or generate media. Treat returned presigned URLs as sensitive bearer credentials. "
    "Do not retry uncertain uploads automatically. Configuration comes from environment variables, never tool arguments."
))


@mcp.tool(icons=plugin_icons(), annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                      idempotentHint=False, openWorldHint=True))
def upload_video(
    file_path: Annotated[str, Field(min_length=1, max_length=4096, strict=True)],
    prefix: Annotated[str | None, Field(max_length=512, strict=True)] = None,
    presign: Annotated[bool | None, Field(strict=True)] = None,
    expires_seconds: Annotated[int | None, Field(ge=60, le=604800, strict=True)] = None,
) -> dict:
    """Upload ONE explicitly selected absolute local video path unchanged and return its COS URL.

    No directories, scanning, cutting, transcoding, deletion, bucket changes or video generation.
    Optional prefix sets an object directory; UUID names avoid collisions. presign defaults to
    configured mode (signed/private by default). expires_seconds is signed-GET lifetime only,
    not object retention. Public mode requires existing public access; never changes ACLs.
    An upload can incur cloud storage/traffic costs. Inspect status, not just presence of a result.
    """
    try:
        return upload(file_path, prefix, presign, expires_seconds)
    except (ConfigError, UploadError) as exc:
        return {"status": "rejected", "url": None, "message": str(exc)}
    except Exception:
        return {"status": "error", "url": None,
                "message": "Upload could not be completed. Check local configuration and SDK installation. Raw errors are suppressed to protect credentials."}


def main() -> None:
    quiet_sdk()
    mcp.run(transport="stdio")


def object_call(operation, *args) -> dict:
    try:
        return operation(*args)
    except (ConfigError, ObjectError) as exc:
        return {"status": "rejected", "message": str(exc)}
    except Exception:
        return {"status": "error", "message": "Operation failed. Raw provider errors are suppressed to protect credentials."}


@mcp.tool(icons=plugin_icons(), annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True))
def download_object(
    object_key: Annotated[str, Field(min_length=1, max_length=1024, strict=True)],
    file_path: Annotated[str, Field(min_length=1, max_length=4096, strict=True)],
) -> dict:
    """Download one exact COS object_key to a new absolute local file_path, up to 5 GiB.

    No URLs, folder scanning or overwrite. Parent directory must exist. Uses a temporary
    file, verifies ETag/length, then publishes without replacing existing files. Cloud egress may cost money.
    """
    return object_call(download, object_key, file_path)


@mcp.tool(icons=plugin_icons(), annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
def delete_object(
    object_key: Annotated[str, Field(min_length=1, max_length=1024, strict=True)],
    confirm: Annotated[bool, Field(strict=True)] = False,
    expected_etag: Annotated[str | None, Field(min_length=1, max_length=256, strict=True)] = None,
) -> dict:
    """Delete one exact COS object, never a prefix, folder, batch or historical version.

    Default confirm=false only inspects and returns ETag. With explicit user authorization,
    use confirm=true and expected_etag from preview. May permanently delete unversioned objects.
    Versioned buckets may add a delete marker; no historical VersionId is ever deleted.
    ETag check is optimistic, not atomic: stop other writers to this key. Never automatically retry.
    """
    return object_call(delete, object_key, confirm, expected_etag)


@mcp.tool(icons=plugin_icons(), annotations=ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True))
def rename_object(
    object_key: Annotated[str, Field(min_length=1, max_length=1024, strict=True)],
    new_object_key: Annotated[str, Field(min_length=1, max_length=1024, strict=True)],
    confirm: Annotated[bool, Field(strict=True)] = False,
    expected_etag: Annotated[str | None, Field(min_length=1, max_length=256, strict=True)] = None,
) -> dict:
    """Rename/move one exact object within the configured bucket: copy, verify, then delete.

    NOT atomic; stop concurrent writers to BOTH keys. Default is preview only. Execute only after
    explicit user authorization with confirm=true and preview expected_etag. Target must not exist.
    Supports up to 5 GiB, never-versioned buckets, bucket-inherited/default object ACL only.
    Requires GetBucketVersioning and GetObjectACL besides normal object read/write/delete permissions.
    Copy failure/verification failure never triggers source deletion. Inspect partial states, do not retry blindly.
    Old URLs become invalid after source deletion. No cross-bucket moves, folder operations or media editing.
    """
    return object_call(rename, object_key, new_object_key, confirm, expected_etag)
