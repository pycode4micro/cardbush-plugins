from __future__ import annotations

import hashlib
import mimetypes
import struct
from dataclasses import dataclass
from pathlib import Path


ALLOWED_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".gif"})


class ImageUploadValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedImageUpload:
    path: Path
    filename: str
    size_bytes: int
    image_signature: str
    content_type: str
    width: int
    height: int


def prepare_image_upload(
    *,
    file_path: str,
    upload_root: str,
    max_size_bytes: int,
    filename: str | None = None,
    required_dimensions: tuple[int, int] | None = None,
) -> PreparedImageUpload:
    """Resolve and validate one server-local image without changing it."""
    root = Path(upload_root).expanduser().resolve()
    requested = Path(file_path).expanduser()
    candidate = requested if requested.is_absolute() else root / requested
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ImageUploadValidationError("Image file does not exist or cannot be read.") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ImageUploadValidationError("Image file must be inside QIANCHUAN_IMAGE_UPLOAD_ROOT.") from exc
    if not resolved.is_file():
        raise ImageUploadValidationError("Image upload source must be a regular file.")
    if resolved.suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))
        raise ImageUploadValidationError(f"Unsupported image extension; allowed: {allowed}.")
    size_bytes = resolved.stat().st_size
    if size_bytes <= 0:
        raise ImageUploadValidationError("Image file is empty.")
    if size_bytes > max_size_bytes:
        raise ImageUploadValidationError(
            f"Image file is {size_bytes} bytes, above the configured {max_size_bytes}-byte limit."
        )
    try:
        width, height = _image_dimensions(resolved)
    except (OSError, ValueError) as exc:
        raise ImageUploadValidationError("Image file is not a decodable image.") from exc
    if width < 1 or height < 1:
        raise ImageUploadValidationError("Image dimensions must be positive.")
    if required_dimensions is not None and (width, height) != required_dimensions:
        expected_width, expected_height = required_dimensions
        raise ImageUploadValidationError(
            f"Image dimensions must be exactly {expected_width}x{expected_height} for this material purpose; "
            f"received {width}x{height}."
        )
    material_filename = (filename or resolved.name).strip()
    if not material_filename or len(material_filename) > 255:
        raise ImageUploadValidationError("Image filename must contain 1 to 255 characters.")
    if Path(material_filename).name != material_filename or any(ord(char) < 32 for char in material_filename):
        raise ImageUploadValidationError("Image filename must not contain paths or control characters.")
    digest = hashlib.md5()  # noqa: S324 - the official API explicitly requires an MD5 signature.
    with resolved.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return PreparedImageUpload(
        path=resolved,
        filename=material_filename,
        size_bytes=size_bytes,
        image_signature=digest.hexdigest(),
        content_type=mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
        width=width,
        height=height,
    )


def _image_dimensions(path: Path) -> tuple[int, int]:
    """Read dimensions from official upload formats without an image-processing dependency."""
    with path.open("rb") as source:
        header = source.read(32)
        source.seek(0)
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            source.read(16)
            width, height = struct.unpack(">II", source.read(8))
            return width, height
        if header.startswith((b"GIF87a", b"GIF89a")):
            source.read(6)
            width, height = struct.unpack("<HH", source.read(4))
            return width, height
        if header.startswith(b"BM"):
            source.seek(18)
            width, height = struct.unpack("<ii", source.read(8))
            return abs(width), abs(height)
        if header.startswith(b"\xff\xd8"):
            return _jpeg_dimensions(source)
    raise ValueError("unsupported or malformed image header")


def _jpeg_dimensions(source) -> tuple[int, int]:
    source.seek(2)
    while True:
        byte = source.read(1)
        while byte == b"\xff":
            byte = source.read(1)
        if not byte:
            break
        marker = byte[0]
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        length_raw = source.read(2)
        if len(length_raw) != 2:
            break
        length = struct.unpack(">H", length_raw)[0]
        if length < 2:
            break
        if marker in {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }:
            payload = source.read(5)
            if len(payload) != 5:
                break
            height, width = struct.unpack(">HH", payload[1:5])
            return width, height
        source.seek(length - 2, 1)
    raise ValueError("malformed JPEG dimensions")
