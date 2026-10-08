"""Facebook (Hermes) content validation.

All validation happens BEFORE opening the browser, so obviously-invalid requests
never launch an automation session. Mirrors the Instagram/Reddit validators.

Facebook rules (this phase — profile OR page):
  - A post must have TEXT, or ONE image, or both (no empty post).
  - At most ONE image (no carousel/multi-image this phase).
  - Only supported image formats.
  - Text is optional when an image is present, and vice versa.
  - target_type must be a known value ("profile" | "page").
This module does not bypass any Facebook restriction — it only checks
Trendzzo-side inputs.
"""

from dataclasses import dataclass, field
from typing import Optional

from app.social_publishing.providers.hermes.facebook.constants import (
    MAX_TEXT_LENGTH,
    MAX_MEDIA_ITEMS,
    SUPPORTED_IMAGE_FORMATS,
    TARGET_TYPES,
    DEFAULT_TARGET_TYPE,
)


@dataclass
class FacebookPostInput:
    """Normalized, validated Facebook post request."""

    text: str
    target_type: str
    media_paths: list[str] = field(default_factory=list)


def validate_post(
    text: str,
    media_paths: Optional[list[str]] = None,
    target_type: str = DEFAULT_TARGET_TYPE,
) -> tuple[Optional[FacebookPostInput], list[str]]:
    """
    Validate and normalize a Facebook post request.

    Returns (FacebookPostInput | None, errors). If errors is non-empty the input
    is None. Never raises.
    """
    errors: list[str] = []
    media_paths = media_paths or []
    text = text or ""

    tt = (target_type or DEFAULT_TARGET_TYPE).strip().lower()
    if tt not in TARGET_TYPES:
        errors.append(
            f"Unknown Facebook target_type '{target_type}'. "
            f"Expected one of: {', '.join(TARGET_TYPES)}"
        )

    # A post needs some content: text and/or a single image.
    if not text.strip() and len(media_paths) == 0:
        errors.append("A Facebook post needs text or an image")

    if len(media_paths) > MAX_MEDIA_ITEMS:
        errors.append(f"At most {MAX_MEDIA_ITEMS} image is supported (no carousel/video this phase)")

    for path in media_paths:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext not in SUPPORTED_IMAGE_FORMATS:
            errors.append(
                f"Unsupported media type '.{ext}'. Facebook workflow supports: "
                f"{', '.join(SUPPORTED_IMAGE_FORMATS)} (video/reels not supported)"
            )

    if len(text) > MAX_TEXT_LENGTH:
        errors.append(f"Text exceeds {MAX_TEXT_LENGTH} characters")

    if errors:
        return None, errors

    return FacebookPostInput(text=text, target_type=tt, media_paths=media_paths), []
