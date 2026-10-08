"""LinkedIn error mapping — converts LinkedIn API errors to Trendzzo-level errors."""

from enum import Enum
from typing import Optional


class LinkedInErrorCode(str, Enum):
    """Trendzzo-level error codes for LinkedIn failures."""

    AUTHENTICATION_REQUIRED = "authentication_required"
    PERMISSION_DENIED = "permission_denied"
    ACCOUNT_NOT_ELIGIBLE = "account_not_eligible"
    INVALID_MEDIA = "invalid_media"
    INVALID_CONTENT = "invalid_content"
    RATE_LIMITED = "rate_limited"
    PLATFORM_UNAVAILABLE = "platform_unavailable"
    UNKNOWN_PLATFORM_ERROR = "unknown_platform_error"


class LinkedInAPIError(Exception):
    """Structured error from a LinkedIn API call."""

    def __init__(
        self,
        code: LinkedInErrorCode,
        message: str,
        http_status: int = 0,
        retryable: bool = False,
    ):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        super().__init__(message)


def classify_linkedin_error(status_code: int, response_body: dict) -> LinkedInAPIError:
    """
    Convert a LinkedIn API error response into a structured LinkedInAPIError.

    Never exposes raw tokens or internal details in the error message.
    """
    message = response_body.get("message", "")
    safe_message = _sanitize_message(message)

    if status_code == 401:
        return LinkedInAPIError(
            code=LinkedInErrorCode.AUTHENTICATION_REQUIRED,
            message=f"LinkedIn authentication failed: {safe_message}",
            http_status=status_code,
            retryable=False,
        )

    if status_code == 403:
        if "role" in message.lower() or "organization" in message.lower():
            return LinkedInAPIError(
                code=LinkedInErrorCode.ACCOUNT_NOT_ELIGIBLE,
                message=f"Insufficient organization role: {safe_message}",
                http_status=status_code,
                retryable=False,
            )
        return LinkedInAPIError(
            code=LinkedInErrorCode.PERMISSION_DENIED,
            message=f"Permission denied: {safe_message}",
            http_status=status_code,
            retryable=False,
        )

    if status_code == 429:
        return LinkedInAPIError(
            code=LinkedInErrorCode.RATE_LIMITED,
            message="LinkedIn rate limit exceeded",
            http_status=status_code,
            retryable=True,
        )

    if status_code == 400:
        lower_msg = message.lower()
        if "image" in lower_msg or "media" in lower_msg or "upload" in lower_msg:
            return LinkedInAPIError(
                code=LinkedInErrorCode.INVALID_MEDIA,
                message=f"Invalid media: {safe_message}",
                http_status=status_code,
                retryable=False,
            )
        return LinkedInAPIError(
            code=LinkedInErrorCode.INVALID_CONTENT,
            message=f"Invalid content: {safe_message}",
            http_status=status_code,
            retryable=False,
        )

    if status_code == 409:
        return LinkedInAPIError(
            code=LinkedInErrorCode.UNKNOWN_PLATFORM_ERROR,
            message="Write conflict — retry the request",
            http_status=status_code,
            retryable=True,
        )

    if status_code >= 500:
        return LinkedInAPIError(
            code=LinkedInErrorCode.PLATFORM_UNAVAILABLE,
            message="LinkedIn API temporarily unavailable",
            http_status=status_code,
            retryable=True,
        )

    return LinkedInAPIError(
        code=LinkedInErrorCode.UNKNOWN_PLATFORM_ERROR,
        message=f"LinkedIn API error ({status_code}): {safe_message}",
        http_status=status_code,
        retryable=status_code >= 500,
    )


def _sanitize_message(message: str) -> str:
    """Remove potentially sensitive data from error messages."""
    sanitized = message[:300] if message else "No details"
    if "access_token" in sanitized.lower() or "bearer" in sanitized.lower():
        return "Authentication error (details redacted)"
    return sanitized
