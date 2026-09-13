"""Safe payload construction for Qianchuan 图文素材库 (carousel) creation.

This module deliberately creates only a material-library carousel.  It has no
product, ad, campaign, or delivery-state fields, so callers cannot accidentally
turn a library upload into a delivery operation.
"""

from __future__ import annotations

from typing import Any


class CarouselLibraryValidationError(ValueError):
    """Raised when a 图文素材库 create request cannot be safely built."""


def build_carousel_create_payload(
    *,
    advertiser_id: int,
    image_ids: list[str | int],
    file_name: str | None = None,
    description: str | None = None,
    audio_id: str | int | None = None,
) -> dict[str, Any]:
    """Build the official ``/2/carousel/create/`` request body.

    The official interface accepts existing image-library identities as
    ``images: [{"image_id": ...}]``.  No product or plan identity is accepted
    here by design.
    """
    if isinstance(advertiser_id, bool) or not isinstance(advertiser_id, int) or advertiser_id < 1:
        raise CarouselLibraryValidationError("advertiser_id must be a positive integer.")
    if not isinstance(image_ids, list):
        raise CarouselLibraryValidationError("image_ids must be a non-empty list.")

    normalized_image_ids = [str(item).strip() for item in image_ids if str(item).strip()]
    if len(normalized_image_ids) < 2:
        raise CarouselLibraryValidationError(
            "image_ids must include at least two non-empty image IDs for a carousel material."
        )
    if len(set(normalized_image_ids)) != len(normalized_image_ids):
        raise CarouselLibraryValidationError("image_ids must not contain duplicates.")

    payload: dict[str, Any] = {
        "advertiser_id": advertiser_id,
        "images": [{"image_id": image_id} for image_id in normalized_image_ids],
    }
    optional_strings = {"file_name": file_name, "description": description}
    for field, value in optional_strings.items():
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise CarouselLibraryValidationError(f"{field} must be a non-empty string when supplied.")
        payload[field] = value.strip()
    if audio_id is not None:
        normalized_audio_id = str(audio_id).strip()
        if not normalized_audio_id:
            raise CarouselLibraryValidationError("audio_id must be non-empty when supplied.")
        payload["audio_id"] = normalized_audio_id
    return payload
