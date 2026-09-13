from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path


ALLOWED_VIDEO_EXTENSIONS = frozenset({".mp4", ".mpeg", ".mpg", ".3gp", ".avi"})


class VideoUploadValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedVideoUpload:
    path: Path
    filename: str
    size_bytes: int
    video_signature: str
    content_type: str


def prepare_video_upload(
    *,
    file_path: str,
    upload_root: str,
    max_size_bytes: int,
    filename: str | None = None,
) -> PreparedVideoUpload:
    """Resolve and validate one server-local video without changing it."""
    root = Path(upload_root).expanduser().resolve()
    requested = Path(file_path).expanduser()
    candidate = requested if requested.is_absolute() else root / requested
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise VideoUploadValidationError("Video file does not exist or cannot be read.") from exc

    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise VideoUploadValidationError(
            "Video file must be inside QIANCHUAN_VIDEO_UPLOAD_ROOT."
        ) from exc
    if not resolved.is_file():
        raise VideoUploadValidationError("Video upload source must be a regular file.")
    if resolved.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_VIDEO_EXTENSIONS))
        raise VideoUploadValidationError(f"Unsupported video extension; allowed: {allowed}.")

    size_bytes = resolved.stat().st_size
    if size_bytes <= 0:
        raise VideoUploadValidationError("Video file is empty.")
    if size_bytes > max_size_bytes:
        raise VideoUploadValidationError(
            f"Video file is {size_bytes} bytes, above the configured {max_size_bytes}-byte limit."
        )

    material_filename = (filename or resolved.name).strip()
    if not material_filename or len(material_filename) > 255:
        raise VideoUploadValidationError("Video filename must contain 1 to 255 characters.")
    if Path(material_filename).name != material_filename or any(
        ord(character) < 32 for character in material_filename
    ):
        raise VideoUploadValidationError("Video filename must not contain paths or control characters.")

    digest = hashlib.md5()  # noqa: S324 - the official API explicitly requires an MD5 signature.
    try:
        with resolved.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise VideoUploadValidationError("Video file could not be read.") from exc

    content_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    return PreparedVideoUpload(
        path=resolved,
        filename=material_filename,
        size_bytes=size_bytes,
        video_signature=digest.hexdigest(),
        content_type=content_type,
    )
