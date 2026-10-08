"""X (Twitter) error mapping — converts X API v2 errors to Trendzzo-level errors.

X API v2 returns errors in a few shapes:
  - OAuth/token errors: {"error": "...", "error_description": "..."}
  - v2 problem responses: {"title": "...", "detail": "...", "type": "...", "status": N}
  - v2 partial errors: {"errors": [{"message": "...", ...}], "data": {...}}

This module never exposes tokens or credentials in error messages.
"""

from enum import Enum
from typing import Optional


class TwitterErrorCode(str, Enum):
    """Trendzzo-level error codes for X (Twitter) failures."""

    AUTHENTICATION_REQUIRED = "authentication_required"
    PERMISSION_DENIED = "permission_denied"
    ACCESS_TIER_REQUIRED = "access_tier_required"
    CREDITS_DEPLETED = "credits_depleted"
    DUPLICATE_CONTENT = "duplicate_content"
    INVALID_MEDIA = "invalid_media"
    INVALID_CONTENT = "invalid_content"
    RATE_LIMITED = "rate_limited"
    PLATFORM_UNAVAILABLE = "platform_unavailable"
    UNKNOWN_PLATFORM_ERROR = "unknown_platform_error"


class TwitterAPIError(Exception):
    """Structured error from an X API call."""

    def __init__(
        self,
        code: TwitterErrorCode,
        message: str,
        http_status: int = 0,
        retryable: bool = False,
    ):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        super().__init__(message)


def classify_twitter_error(status_code: int, response_body: dict) -> TwitterAPIError:
    """
    Convert an X API error response into a structured TwitterAPIError.

    Handles both OAuth-style and v2 problem/partial-error shapes.
    Never exposes tokens or sensitive internal data in messages.
    """
    raw_detail = _extract_detail(response_body)
    safe_detail = _sanitize_message(raw_detail)
    lower = raw_detail.lower()

    # ── 401 — expired/invalid token ──────────────────────────────────────────
    if status_code == 401:
        return TwitterAPIError(
            code=TwitterErrorCode.AUTHENTICATION_REQUIRED,
            message=f"X authentication expired or invalid: {safe_detail}",
            http_status=status_code,
            retryable=False,
        )

    # ── 402 — payment required: X posting credits exhausted / plan needed ─────
    if status_code == 402:
        return TwitterAPIError(
            code=TwitterErrorCode.CREDITS_DEPLETED,
            message=(
                "X rejected the post: your X API plan is out of posting credits "
                "(HTTP 402). Add credits or upgrade your X API plan to publish."
            ),
            http_status=status_code,
            retryable=False,
        )

    # ── 403 — forbidden: could be permission, duplicate, or access tier ──────
    if status_code == 403:
        if "duplicate" in lower:
            return TwitterAPIError(
                code=TwitterErrorCode.DUPLICATE_CONTENT,
                message="X rejected the post as a duplicate of a recent post",
                http_status=status_code,
                retryable=False,
            )
        if "access" in lower and ("level" in lower or "product" in lower or "tier" in lower):
            return TwitterAPIError(
                code=TwitterErrorCode.ACCESS_TIER_REQUIRED,
                message=(
                    "Your X app's access tier does not permit this action. "
                    "Posting requires a paid X API tier."
                ),
                http_status=status_code,
                retryable=False,
            )
        return TwitterAPIError(
            code=TwitterErrorCode.PERMISSION_DENIED,
            message=f"X permission denied: {safe_detail}",
            http_status=status_code,
            retryable=False,
        )

    # ── 429 — rate limited ───────────────────────────────────────────────────
    if status_code == 429:
        return TwitterAPIError(
            code=TwitterErrorCode.RATE_LIMITED,
            message="X rate limit exceeded",
            http_status=status_code,
            retryable=True,
        )

    # ── 400 — bad request: content vs media ──────────────────────────────────
    if status_code == 400:
        if "media" in lower or "image" in lower or "video" in lower:
            return TwitterAPIError(
                code=TwitterErrorCode.INVALID_MEDIA,
                message=f"Invalid media: {safe_detail}",
                http_status=status_code,
                retryable=False,
            )
        return TwitterAPIError(
            code=TwitterErrorCode.INVALID_CONTENT,
            message=f"Invalid content: {safe_detail}",
            http_status=status_code,
            retryable=False,
        )

    # ── 5xx — platform unavailable ───────────────────────────────────────────
    if status_code >= 500:
        return TwitterAPIError(
            code=TwitterErrorCode.PLATFORM_UNAVAILABLE,
            message="X API temporarily unavailable",
            http_status=status_code,
            retryable=True,
        )

    return TwitterAPIError(
        code=TwitterErrorCode.UNKNOWN_PLATFORM_ERROR,
        message=f"X API error ({status_code}): {safe_detail}",
        http_status=status_code,
        retryable=status_code >= 500,
    )


def _extract_detail(response_body: dict) -> str:
    """Pull a human-readable message out of any known X error shape."""
    if not isinstance(response_body, dict):
        return "No details"

    # OAuth-style
    if "error_description" in response_body:
        return str(response_body.get("error_description") or "")
    if "error" in response_body and isinstance(response_body["error"], str):
        return str(response_body["error"])

    # v2 partial errors: {"errors": [{"message": ...}]}
    errors = response_body.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("message") or first.get("detail") or "")

    # v2 problem response
    if "detail" in response_body:
        return str(response_body.get("detail") or "")
    if "title" in response_body:
        return str(response_body.get("title") or "")

    return "No details"


def _sanitize_message(message: str) -> str:
    """Remove sensitive data from error messages."""
    sanitized = (message or "No details")[:300]
    lower = sanitized.lower()
    if "access_token" in lower or "bearer" in lower or "refresh_token" in lower:
        return "Authentication error (details redacted)"
    return sanitized
