"""X (Twitter) media validation — validates content before creating jobs.

X posts support:
  - Text-only posts (text within the character limit)
  - Image posts (up to 4 images)
  - Video posts (a single video)

X does NOT require media (text-only is valid), unlike Instagram.
"""

from urllib.parse import urlparse

from app.social_publishing.integrations.twitter.constants import (
    MAX_MEDIA_ITEMS,
    MAX_TWEET_LENGTH,
    SUPPORTED_IMAGE_FORMATS,
)


def validate_for_publishing(content: str, media_urls: list[str]) -> list[str]:
    """
    Validate content for X publishing.

    Returns a list of error messages. Empty list = valid.
    Text-only posts are valid as long as there is text or media.
    """
    errors: list[str] = []

    has_content = bool(content and content.strip())
    has_media = bool(media_urls)

    if not has_content and not has_media:
        errors.append("Post must have text or media")

    # Character limit (measured in characters; X counts differ for URLs/emoji
    # but the character count is a safe conservative pre-check)
    if content and len(content) > MAX_TWEET_LENGTH:
        errors.append(
            f"Post exceeds {MAX_TWEET_LENGTH} character limit "
            f"({len(content)} characters)"
        )

    # Media count
    if len(media_urls) > MAX_MEDIA_ITEMS:
        errors.append(f"X allows at most {MAX_MEDIA_ITEMS} media items per post")

    # Media URL validation
    for url in media_urls:
        errors.extend(_validate_media_url(url))

    return errors


def _validate_media_url(url: str) -> list[str]:
    """Validate a single media URL is well-formed and uses http/https."""
    errors: list[str] = []

    if not url or not url.strip():
        errors.append("Empty media URL")
        return errors

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        errors.append(f"Media URL must use http/https: {_truncate(url)}")
    if not parsed.netloc:
        errors.append(f"Invalid media URL: {_truncate(url)}")

    return errors


def detect_media_type(media_urls: list[str]) -> str:
    """Determine the X post type from the media URLs."""
    if not media_urls:
        return "text"

    first = media_urls[0].lower()
    video_extensions = (".mp4", ".mov", ".m4v")
    if any(first.endswith(ext) or f"{ext}?" in first for ext in video_extensions):
        return "video"
    return "image"


def is_supported_image(url: str) -> bool:
    """Check whether a URL's extension is a supported image format."""
    path = urlparse(url).path.lower()
    return any(path.endswith(f".{ext}") for ext in SUPPORTED_IMAGE_FORMATS)


def _truncate(url: str) -> str:
    """Truncate URL for safe display (drops query params)."""
    parsed = urlparse(url)
    path = parsed.path[:50] + ("..." if len(parsed.path) > 50 else "")
    return f"{parsed.scheme}://{parsed.netloc}{path}"
