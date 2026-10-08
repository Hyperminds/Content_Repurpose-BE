"""Reddit content + subreddit validation (Phase 14/15).

All validation happens BEFORE opening the browser, so obviously-invalid
requests never launch an automation session. This module does not attempt to
bypass any Reddit content restrictions — it only checks Trendzzo-side inputs.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from app.social_publishing.providers.hermes.reddit.constants import (
    MAX_BODY_LENGTH,
    MAX_MEDIA_ITEMS,
    MAX_TITLE_LENGTH,
    MIN_TITLE_LENGTH,
    SUPPORTED_IMAGE_FORMATS,
)

# Reddit subreddit names: 3-21 chars, letters/digits/underscore (plus we accept
# an optional "r/" prefix and normalize it away).
_SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")


@dataclass
class RedditPostInput:
    """Normalized, validated Reddit post request."""

    title: str
    body: str
    subreddit: str
    media_paths: list[str] = field(default_factory=list)


def normalize_subreddit(raw: str) -> str:
    """
    Normalize a subreddit value: strip whitespace, leading '/', and 'r/' prefix.

    'r/example' -> 'example'; '/r/Example/' -> 'Example'. Does not lowercase
    (subreddit names are case-insensitive on Reddit but display-cased).
    """
    if not raw:
        return ""
    s = raw.strip().strip("/")
    if s.lower().startswith("r/"):
        s = s[2:]
    return s.strip("/")


def validate_post(
    title: str,
    body: str,
    subreddit: str,
    media_paths: Optional[list[str]] = None,
) -> tuple[Optional[RedditPostInput], list[str]]:
    """
    Validate and normalize a Reddit post request.

    Returns (RedditPostInput | None, errors). If errors is non-empty the input
    is None. Never raises.
    """
    errors: list[str] = []
    media_paths = media_paths or []

    sub = normalize_subreddit(subreddit)
    if not sub:
        errors.append("Subreddit is required")
    elif not _SUBREDDIT_RE.match(sub):
        errors.append("Invalid subreddit name")

    t = (title or "").strip()
    if len(t) < MIN_TITLE_LENGTH:
        errors.append("Title is required")
    elif len(t) > MAX_TITLE_LENGTH:
        errors.append(f"Title exceeds {MAX_TITLE_LENGTH} characters")

    if body and len(body) > MAX_BODY_LENGTH:
        errors.append(f"Body exceeds {MAX_BODY_LENGTH} characters")

    # A post needs at least a title (text post) — body/media optional. But if
    # there is no body and no media, that's still a valid text (title-only) post
    # on Reddit, so we don't reject it.

    if len(media_paths) > MAX_MEDIA_ITEMS:
        errors.append(f"At most {MAX_MEDIA_ITEMS} media item(s) supported")

    for path in media_paths:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext not in SUPPORTED_IMAGE_FORMATS:
            errors.append(
                f"Unsupported media type '.{ext}'. Reddit workflow supports: "
                f"{', '.join(SUPPORTED_IMAGE_FORMATS)} (video not yet supported)"
            )

    if errors:
        return None, errors

    return RedditPostInput(title=t, body=body or "", subreddit=sub, media_paths=media_paths), []
