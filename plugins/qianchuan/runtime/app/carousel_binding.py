"""Validated payload construction for PC uni-promotion carousel (图文) creatives.

``material_id`` identifies a report/library item, but it is *not* accepted by the
PC full-domain create or material-add APIs. Those APIs accept a published Aweme
carousel identity (``aweme_carousel_id``) or a carousel-library identity
(``carousel_id``). Keeping that distinction here prevents an otherwise-valid read
response from becoming an invalid write payload.
"""

from __future__ import annotations

from typing import Any


class CarouselBindingValidationError(ValueError):
    """Raised when a carousel payload cannot be safely constructed."""


_REFERENCE_FIELDS = ("aweme_carousel_id", "carousel_id")


def build_uni_promotion_carousel_binding_payload(
    *,
    advertiser_id: int,
    ad_id: int,
    bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one official material-add request from product-scoped carousels.

    A binding is ``{product_id, carousels}``. Each carousel contains exactly one
    identity returned by an official carousel read endpoint. Inline images and
    report-only ``material_id`` values are deliberately rejected: neither is part
    of the current uni-promotion material-add/create request contract.
    """
    if advertiser_id < 1 or ad_id < 1:
        raise CarouselBindingValidationError("advertiser_id and ad_id must be positive.")
    if not isinstance(bindings, list) or not bindings:
        raise CarouselBindingValidationError("Provide at least one product carousel binding.")

    seen_products: set[int] = set()
    creatives: list[dict[str, Any]] = []
    for index, binding in enumerate(bindings):
        prefix = f"bindings[{index}]"
        if not isinstance(binding, dict):
            raise CarouselBindingValidationError(f"{prefix} must be an object.")
        product_id = _positive_int(binding.get("product_id"), f"{prefix}.product_id")
        if product_id in seen_products:
            raise CarouselBindingValidationError(
                f"{prefix}.product_id is duplicated; use one binding per product."
            )
        seen_products.add(product_id)
        carousels = binding.get("carousels")
        if not isinstance(carousels, list) or not carousels:
            raise CarouselBindingValidationError(f"{prefix}.carousels must be a non-empty list.")
        creatives.append(
            {
                "product_id": product_id,
                "carousel_material": [
                    _normalize_carousel(item, f"{prefix}.carousels[{item_index}]")
                    for item_index, item in enumerate(carousels)
                ],
            }
        )
    return {
        "advertiser_id": advertiser_id,
        "ad_id": ad_id,
        "multi_product_creative_list": creatives,
    }


def normalize_carousel_material(item: dict[str, Any], prefix: str = "carousel") -> dict[str, Any]:
    """Validate one carousel reference for a uni-promotion create/update payload."""
    return _normalize_carousel(item, prefix)


def _normalize_carousel(item: Any, prefix: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise CarouselBindingValidationError(f"{prefix} must be an object.")
    references = [field for field in _REFERENCE_FIELDS if item.get(field) not in (None, "")]
    if len(references) != 1:
        raise CarouselBindingValidationError(
            f"{prefix} must contain exactly one of: {', '.join(_REFERENCE_FIELDS)}. "
            "Do not send material_id, title, description, or image here."
        )
    unsupported = {key for key, value in item.items() if value not in (None, "")} - set(references)
    if unsupported:
        raise CarouselBindingValidationError(
            f"{prefix} contains unsupported fields for uni-promotion: {', '.join(sorted(unsupported))}."
        )
    field = references[0]
    return {field: _positive_int(item[field], f"{prefix}.{field}")}


def _positive_int(value: Any, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CarouselBindingValidationError(f"{name} must be a positive integer.") from exc
    if parsed < 1:
        raise CarouselBindingValidationError(f"{name} must be a positive integer.")
    return parsed
