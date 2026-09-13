"""Validated payload construction for uni-promotion product-card images.

The official endpoint is unusually sensitive: a product is represented once, every
square image is a separate ``image_material`` item, and the product-card title is
sent once per product.  Keeping that shape here prevents agents from having to
hand-assemble nested payloads.
"""

from __future__ import annotations

from typing import Any


class ProductCardBindingValidationError(ValueError):
    """Raised when a product-card binding cannot be made safely."""


def build_uni_promotion_product_card_binding_payload(
    *,
    advertiser_id: int,
    ad_id: int,
    bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the official material-add payload from one binding per product.

    Each binding requires ``product_id``, a product-specific ``title`` and one or
    more uploaded ``image_ids``.  An image ID may only occur once in a request.
    """
    if advertiser_id < 1 or ad_id < 1:
        raise ProductCardBindingValidationError("advertiser_id and ad_id must be positive.")
    if not isinstance(bindings, list) or not bindings:
        raise ProductCardBindingValidationError("Provide at least one product-card binding.")

    seen_products: set[int] = set()
    seen_images: set[str] = set()
    creatives: list[dict[str, Any]] = []
    for index, binding in enumerate(bindings, start=1):
        prefix = f"bindings[{index - 1}]"
        if not isinstance(binding, dict):
            raise ProductCardBindingValidationError(f"{prefix} must be an object.")
        try:
            product_id = int(binding.get("product_id"))
        except (TypeError, ValueError) as exc:
            raise ProductCardBindingValidationError(f"{prefix}.product_id must be a positive integer.") from exc
        if product_id < 1:
            raise ProductCardBindingValidationError(f"{prefix}.product_id must be a positive integer.")
        if product_id in seen_products:
            raise ProductCardBindingValidationError(
                f"{prefix}.product_id is duplicated; use one binding per product."
            )
        seen_products.add(product_id)

        title = str(binding.get("title") or "").strip()
        if not 5 <= len(title) <= 55:
            raise ProductCardBindingValidationError(
                f"{prefix}.title must contain 5 to 55 characters for a product card."
            )
        image_ids = binding.get("image_ids")
        if not isinstance(image_ids, list) or not image_ids:
            raise ProductCardBindingValidationError(f"{prefix}.image_ids must be a non-empty list.")
        images: list[dict[str, Any]] = []
        for image_index, image_id in enumerate(image_ids):
            normalized_image_id = str(image_id or "").strip()
            if not normalized_image_id:
                raise ProductCardBindingValidationError(
                    f"{prefix}.image_ids[{image_index}] must not be empty."
                )
            if normalized_image_id in seen_images:
                raise ProductCardBindingValidationError(
                    f"{prefix}.image_ids[{image_index}] is duplicated across bindings."
                )
            seen_images.add(normalized_image_id)
            # The official endpoint rejects multiple IDs inside one image_material.
            images.append({"image_ids": [normalized_image_id], "image_mode": "SQUARE"})
        creatives.append(
            {
                "product_id": product_id,
                "image_material": images,
                "title_material": [{"title": title, "title_type": "COMMODITY_CARD"}],
            }
        )
    return {
        "advertiser_id": advertiser_id,
        "ad_id": ad_id,
        "multi_product_creative_list": creatives,
    }
