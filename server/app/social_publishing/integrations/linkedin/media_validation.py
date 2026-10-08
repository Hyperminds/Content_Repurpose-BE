"""LinkedIn media validation — validates media before creating publishing jobs.

LinkedIn supports:
  - Text-only posts (no media required)
  - Single image posts (JPG, PNG, GIF)
  - Video posts
  - Multi-image posts (organic)
  - Article posts (URL + thumbnail)
"""

from urllib.parse import urlparse

from app.social_publishing.integrations.linkedin.constants import (
    MAX_COMMENTARY_LENGTH,
    MAX_IMAGES_PER_POST,
    SUPPORTED_IMAGE_FORMATS,
)


def validate_for_publishing(
    content: str,
    media_urls: list[str],
    media_type: str = "text",
) -> list[str]:
    """
    Validate content and media for LinkedIn publishing.

    Returns a list of error messages. Empty list = valid.

    LinkedIn allows text-only posts, so empty media_urls is valid.
    """
    errors: list[str] = []

    # Content validation
    if not content or not content.strip():
        if not media_urls:
            errors.append("Post must have content text or media")

    if content and len(content) > MAX_COMMENTARY_LENGTH:
        errors.append(f"Content exceeds {MAX_COMMENTARY_LENGTH} character limit")

    # Media URL validation
    for url in media_urls:
        url_errors = _validate_media_url(url, media_type)
        errors.extend(url_errors)

    # Multi-image limit
    if media_type == "image" and len(media_urls) > MAX_IMAGES_PER_POST:
        errors.append(f"Maximum {MAX_IMAGES_PER_POST} images per post")

    return errors


def _validate_media_url(url: str, media_type: str) -> list[str]:
    """Validate a single media URL."""
    errors: list[str] = []

    if not url or not url.strip():
        errors.append("Empty media URL")
        return errors

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        errors.append(f"Media URL must use http/https: {_truncate(url)}")

    if not parsed.netloc:
        errors.append(f"Invalid media URL: {_truncate(url)}")

    # Image format check (only for image media type)
    if media_type == "image":
        path_lower = parsed.path.lower()
        unsupported = [".svg", ".bmp", ".tiff", ".webp", ".heic"]
        if any(path_lower.endswith(ext) for ext in unsupported):
            errors.append(
                f"Unsupported image format. LinkedIn supports: "
                f"{', '.join(SUPPORTED_IMAGE_FORMATS)}. URL: {_truncate(url)}"
            )

    return errors


def detect_media_type(media_urls: list[str]) -> str:
    """Detect the appropriate LinkedIn media type from URLs."""
    if not media_urls:
        return "text"

    if len(media_urls) > 1:
        return "multi_image"

    url = media_urls[0].lower()
    video_extensions = (".mp4", ".mov", ".avi", ".wmv")
    if any(url.endswith(ext) or f"{ext}?" in url for ext in video_extensions):
        return "video"

    return "image"


def _truncate(url: str) -> str:
    """Truncate URL for safe display in errors (strip query params)."""
    parsed = urlparse(url)
    path = parsed.path[:50] + ("..." if len(parsed.path) > 50 else "")
    return f"{parsed.scheme}://{parsed.netloc}{path}"
