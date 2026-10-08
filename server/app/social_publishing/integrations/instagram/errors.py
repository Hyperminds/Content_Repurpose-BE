"""Instagram error mapping — converts Meta API errors to Trendzzo-level errors.

Maps HTTP status codes and Meta error structures into standardized error
categories that the publishing worker understands.
"""

from enum import Enum
from typing import Optional


class InstagramErrorCode(str, Enum):
    """Trendzzo-level error codes for Instagram failures."""

    AUTHENTICATION_REQUIRED = "authentication_required"
    PERMISSION_DENIED = "permission_denied"
    ACCOUNT_NOT_ELIGIBLE = "account_not_eligible"
    INVALID_MEDIA = "invalid_media"
    INVALID_CONTENT = "invalid_content"
    RATE_LIMITED = "rate_limited"
    PLATFORM_UNAVAILABLE = "platform_unavailable"
    UNKNOWN_PLATFORM_ERROR = "unknown_platform_error"


class InstagramAPIError(Exception):
    """Structured error from an Instagram API call."""

    def __init__(
        self,
        code: InstagramErrorCode,
        message: str,
        meta_error_code: Optional[int] = None,
        meta_error_subcode: Optional[int] = None,
        retryable: bool = False,
    ):
        self.code = code
        self.message = message
        self.meta_error_code = meta_error_code
        self.meta_error_subcode = meta_error_subcode
        self.retryable = retryable
        super().__init__(message)


def classify_meta_error(status_code: int, response_body: dict) -> InstagramAPIError:
    """
    Convert a Meta API error response into a structured InstagramAPIError.

    Does NOT expose raw tokens or sensitive data in the error message.
    """
    error_data = response_body.get("error", {})
    meta_code = error_data.get("code", 0)
    meta_subcode = error_data.get("error_subcode", 0)
    meta_type = error_data.get("type", "")
    meta_message = error_data.get("message", "")

    # Sanitize: never include token-related info in error messages
    safe_message = _sanitize_error_message(meta_message)

    # Authentication errors
    if status_code == 401 or meta_type == "OAuthException":
        return InstagramAPIError(
            code=InstagramErrorCode.AUTHENTICATION_REQUIRED,
            message=f"Instagram authentication failed: {safe_message}",
            meta_error_code=meta_code,
            retryable=False,
        )

    # Permission errors
    if meta_code == 10 or "permission" in meta_message.lower():
        return InstagramAPIError(
            code=InstagramErrorCode.PERMISSION_DENIED,
            message=f"Insufficient permissions: {safe_message}",
            meta_error_code=meta_code,
            retryable=False,
        )

    # Rate limiting
    if status_code == 429 or meta_code == 4 or meta_code == 32:
        return InstagramAPIError(
            code=InstagramErrorCode.RATE_LIMITED,
            message="Instagram rate limit reached",
            meta_error_code=meta_code,
            retryable=True,
        )

    # Invalid media (various sub-codes)
    if meta_code == 36003 or "media" in meta_message.lower():
        return InstagramAPIError(
            code=InstagramErrorCode.INVALID_MEDIA,
            message=f"Invalid media: {safe_message}",
            meta_error_code=meta_code,
            retryable=False,
        )

    # Content policy
    if "policy" in meta_message.lower() or "content" in meta_message.lower():
        return InstagramAPIError(
            code=InstagramErrorCode.INVALID_CONTENT,
            message=f"Content rejected: {safe_message}",
            meta_error_code=meta_code,
            retryable=False,
        )

    # Platform unavailable (5xx)
    if status_code >= 500:
        return InstagramAPIError(
            code=InstagramErrorCode.PLATFORM_UNAVAILABLE,
            message="Instagram API temporarily unavailable",
            meta_error_code=meta_code,
            retryable=True,
        )

    # Eligibility issues
    if "not eligible" in meta_message.lower() or "professional" in meta_message.lower():
        return InstagramAPIError(
            code=InstagramErrorCode.ACCOUNT_NOT_ELIGIBLE,
            message=f"Account not eligible: {safe_message}",
            meta_error_code=meta_code,
            retryable=False,
        )

    # Unknown
    return InstagramAPIError(
        code=InstagramErrorCode.UNKNOWN_PLATFORM_ERROR,
        message=f"Instagram API error ({status_code}): {safe_message}",
        meta_error_code=meta_code,
        meta_error_subcode=meta_subcode,
        retryable=status_code >= 500,
    )


def _sanitize_error_message(message: str) -> str:
    """Remove any potentially sensitive data from error messages."""
    # Truncate to prevent huge error payloads
    sanitized = message[:300] if message else "No details available"
    # Never include anything that looks like a token
    if "access_token" in sanitized.lower() or "bearer" in sanitized.lower():
        return "Authentication error (details redacted)"
    return sanitized
