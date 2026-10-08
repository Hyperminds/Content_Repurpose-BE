"""Instagram (Hermes) content validation.

All validation happens BEFORE opening the browser, so obviously-invalid requests
never launch an automation session. Mirrors the Reddit validator's shape.

Instagram rules (single-image phase):
  - EXACTLY ONE image is required (Instagram has no text-only post).
  - Only supported image formats.
  - Caption is OPTIONAL, max 2200 chars.
This module does not bypass any Instagram restriction — it only checks
Trendzzo-side inputs.
"""

from dataclasses import dataclass, field
from typing import Optional

from app.social_publishing.providers.hermes.instagram.constants import (
    MAX_CAPTION_LENGTH,
    MAX_MEDIA_ITEMS,
    MIN_MEDIA_ITEMS,
    SUPPORTED_IMAGE_FORMATS,
)


@dataclass
class InstagramPostInput:
    """Normalized, validated Instagram post request."""

    caption: str
    media_paths: list[str] = field(default_factory=list)


def validate_post(
    caption: str,
    media_paths: Optional[list[str]] = None,
) -> tuple[Optional[InstagramPostInput], list[str]]:
    """
    Validate and normalize an Instagram post request.

    Returns (InstagramPostInput | None, errors). If errors is non-empty the
    input is None. Never raises.
    """
    errors: list[str] = []
    media_paths = media_paths or []

    # Instagram requires media — enforce single image (this phase).
    if len(media_paths) < MIN_MEDIA_ITEMS:
        errors.append("An image is required for an Instagram post")
    elif len(media_paths) > MAX_MEDIA_ITEMS:
        errors.append(f"At most {MAX_MEDIA_ITEMS} image is supported")

    for path in media_paths:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext not in SUPPORTED_IMAGE_FORMATS:
            errors.append(
                f"Unsupported media type '.{ext}'. Instagram workflow supports: "
                f"{', '.join(SUPPORTED_IMAGE_FORMATS)} (video/reels not supported)"
            )

    caption = caption or ""
    if len(caption) > MAX_CAPTION_LENGTH:
        errors.append(f"Caption exceeds {MAX_CAPTION_LENGTH} characters")

    if errors:
        return None, errors

    return InstagramPostInput(caption=caption, media_paths=media_paths), []
