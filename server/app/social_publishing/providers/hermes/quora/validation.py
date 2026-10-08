"""Quora (Hermes) content validation.

All validation happens BEFORE opening the browser, so obviously-invalid requests
never launch an automation session. Mirrors the Facebook/Instagram validators.

Quora rules (this phase — profile/space Post):
  - A post must have TEXT, or ONE image, or both (no empty post).
  - At most ONE image (no carousel/multi-image this phase).
  - Only supported image formats.
  - Text is optional when an image is present, and vice versa.
This module does not bypass any Quora restriction — it only checks Trendzzo-side
inputs.
"""

from dataclasses import dataclass, field
from typing import Optional

from app.social_publishing.providers.hermes.quora.constants import (
    MAX_TEXT_LENGTH,
    MAX_MEDIA_ITEMS,
    SUPPORTED_IMAGE_FORMATS,
)


@dataclass
class QuoraPostInput:
    """Normalized, validated Quora post request."""

    text: str
    media_paths: list[str] = field(default_factory=list)


def validate_post(
    text: str,
    media_paths: Optional[list[str]] = None,
) -> tuple[Optional[QuoraPostInput], list[str]]:
    """
    Validate and normalize a Quora post request.

    Returns (QuoraPostInput | None, errors). If errors is non-empty the input is
    None. Never raises.
    """
    errors: list[str] = []
    media_paths = media_paths or []
    text = text or ""

    # A post needs some content: text and/or a single image.
    if not text.strip() and len(media_paths) == 0:
        errors.append("A Quora post needs text or an image")

    if len(media_paths) > MAX_MEDIA_ITEMS:
        errors.append(f"At most {MAX_MEDIA_ITEMS} image is supported (no carousel/video this phase)")

    for path in media_paths:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext not in SUPPORTED_IMAGE_FORMATS:
            errors.append(
                f"Unsupported media type '.{ext}'. Quora workflow supports: "
                f"{', '.join(SUPPORTED_IMAGE_FORMATS)} (video not supported)"
            )

    if len(text) > MAX_TEXT_LENGTH:
        errors.append(f"Text exceeds {MAX_TEXT_LENGTH} characters")

    if errors:
        return None, errors

    return QuoraPostInput(text=text, media_paths=media_paths), []
