"""Instagram media validation — checks media before creating publishing jobs.

Validates:
  - At least one media URL is provided (Instagram requires image or video)
  - Image format is JPEG (only format supported)
  - URLs are publicly accessible (https://)
  - Caption length within limits
  - Hashtag count within limits
  - Carousel item count within limits
"""

from typing import Optional
from urllib.parse import urlparse

from app.social_publishing.integrations.instagram.constants import (
    MAX_CAPTION_LENGTH,
    MAX_CAROUSEL_ITEMS,
    MAX_HASHTAGS,
    SUPPORTED_IMAGE_FORMATS,
)


def validate_for_publishing(
    content: str,
    media_urls: list[str],
    media_type: str = "image",
) -> list[str]:
    """
    Validate content and media for Instagram publishing.

    Returns a list of validation error messages. Empty list = valid.
    """
    errors: list[str] = []

    # Instagram requires at least one media item
    if not media_urls:
        errors.append("Instagram requires at least one image or video URL")
        return errors  # No point validating further

    # Validate each media URL
    for url in media_urls:
        url_errors = _validate_media_url(url, media_type)
        errors.extend(url_errors)

    # Caption validation
    if content and len(content) > MAX_CAPTION_LENGTH:
        errors.append(f"Caption exceeds {MAX_CAPTION_LENGTH} character limit")

    # Hashtag count
    if content:
        hashtag_count = content.count("#")
        if hashtag_count > MAX_HASHTAGS:
            errors.append(f"Too many hashtags ({hashtag_count}). Maximum is {MAX_HASHTAGS}")

    # Carousel limits
    if len(media_urls) > MAX_CAROUSEL_ITEMS:
        errors.append(f"Carousel limited to {MAX_CAROUSEL_ITEMS} items, got {len(media_urls)}")

    return errors


def _validate_media_url(url: str, media_type: str) -> list[str]:
    """Validate a single media URL."""
    errors: list[str] = []

    if not url or not url.strip():
        errors.append("Empty media URL provided")
        return errors

    parsed = urlparse(url)

    # Must be a public URL (Meta fetches via cURL)
    if parsed.scheme not in ("http", "https"):
        errors.append(f"Media URL must use http/https: {_truncate_url(url)}")

    if not parsed.netloc:
        errors.append(f"Invalid media URL: {_truncate_url(url)}")

    # Image format validation
    if media_type == "image":
        path_lower = parsed.path.lower()
        has_valid_extension = any(
            path_lower.endswith(f".{fmt}") for fmt in SUPPORTED_IMAGE_FORMATS
        )
        # Also check query-string based URLs (CDNs often don't have extensions)
        # Only flag if there's a clearly wrong extension
        wrong_extensions = [".png", ".webp", ".gif", ".bmp", ".tiff", ".svg"]
        has_wrong_extension = any(path_lower.endswith(ext) for ext in wrong_extensions)

        if has_wrong_extension:
            errors.append(
                f"Instagram only supports JPEG images. "
                f"Detected unsupported format in URL: {_truncate_url(url)}"
            )

    return errors


def detect_media_type(media_urls: list[str]) -> str:
    """Detect the media type from URLs for the Instagram container."""
    if not media_urls:
        return "image"

    if len(media_urls) > 1:
        return "carousel"

    url = media_urls[0].lower()
    video_extensions = (".mp4", ".mov", ".avi", ".mkv")
    if any(url.endswith(ext) or ext + "?" in url for ext in video_extensions):
        return "video"

    return "image"


def _truncate_url(url: str) -> str:
    """Truncate a URL for safe error messages (no query params that might have tokens)."""
    parsed = urlparse(url)
    safe = f"{parsed.scheme}://{parsed.netloc}{parsed.path[:50]}"
    if len(parsed.path) > 50:
        safe += "..."
    return safe
