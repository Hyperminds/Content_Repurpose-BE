"""Facebook media validation — validates content before creating publishing jobs.

Facebook Pages support:
  - Text-only posts (message only)
  - Link posts (message + URL)
  - Photo posts (image URL or upload)
  - Video posts (upload)
"""

from urllib.parse import urlparse

from app.social_publishing.integrations.facebook.constants import (
    MAX_MESSAGE_LENGTH,
    SUPPORTED_PHOTO_FORMATS,
)


def validate_for_publishing(
    content: str,
    media_urls: list[str],
    link_url: str = "",
) -> list[str]:
    """
    Validate content for Facebook Page publishing.

    Returns a list of error messages. Empty list = valid.

    Facebook allows text-only posts, so empty media is valid as long as
    there is a message or link.
    """
    errors: list[str] = []

    # At least message or link or media required
    has_content = bool(content and content.strip())
    has_link = bool(link_url and link_url.strip())
    has_media = bool(media_urls)

    if not has_content and not has_link and not has_media:
        errors.append("Post must have message text, a link, or media")

    # Message length
    if content and len(content) > MAX_MESSAGE_LENGTH:
        errors.append(f"Message exceeds {MAX_MESSAGE_LENGTH} character limit")

    # Link URL validation
    if link_url:
        link_errors = _validate_url(link_url)
        errors.extend(link_errors)

    # Media URL validation
    for url in media_urls:
        url_errors = _validate_media_url(url)
        errors.extend(url_errors)

    return errors


def _validate_url(url: str) -> list[str]:
    """Validate a URL is well-formed and uses http/https."""
    errors: list[str] = []
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        errors.append(f"URL must use http/https: {_truncate(url)}")
    if not parsed.netloc:
        errors.append(f"Invalid URL: {_truncate(url)}")
    return errors


def _validate_media_url(url: str) -> list[str]:
    """Validate a media URL."""
    errors: list[str] = []

    if not url or not url.strip():
        errors.append("Empty media URL")
        return errors

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        errors.append(f"Media URL must use http/https: {_truncate(url)}")

    if not parsed.netloc:
        errors.append(f"Invalid media URL: {_truncate(url)}")

    # Check for explicitly unsupported formats
    path_lower = parsed.path.lower()
    unsupported = [".svg", ".bmp", ".tiff", ".webp", ".heic"]
    if any(path_lower.endswith(ext) for ext in unsupported):
        errors.append(
            f"Unsupported image format. Facebook supports: "
            f"{', '.join(SUPPORTED_PHOTO_FORMATS)}. URL: {_truncate(url)}"
        )

    return errors


def detect_post_type(content: str, media_urls: list[str], link_url: str = "") -> str:
    """Determine the Facebook post type based on inputs."""
    if media_urls:
        url = media_urls[0].lower()
        video_extensions = (".mp4", ".mov", ".avi", ".wmv")
        if any(url.endswith(ext) or f"{ext}?" in url for ext in video_extensions):
            return "video"
        return "photo"

    if link_url:
        return "link"

    return "text"


def _truncate(url: str) -> str:
    """Truncate URL for safe display (no query params)."""
    parsed = urlparse(url)
    path = parsed.path[:50] + ("..." if len(parsed.path) > 50 else "")
    return f"{parsed.scheme}://{parsed.netloc}{path}"
