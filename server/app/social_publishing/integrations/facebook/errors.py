"""Facebook error mapping — converts Graph API errors to Trendzzo-level errors."""

from enum import Enum
from typing import Optional


class FacebookErrorCode(str, Enum):
    """Trendzzo-level error codes for Facebook failures."""

    AUTHENTICATION_REQUIRED = "authentication_required"
    PERMISSION_DENIED = "permission_denied"
    ACCOUNT_NOT_ELIGIBLE = "account_not_eligible"
    INVALID_MEDIA = "invalid_media"
    INVALID_CONTENT = "invalid_content"
    RATE_LIMITED = "rate_limited"
    PLATFORM_UNAVAILABLE = "platform_unavailable"
    UNKNOWN_PLATFORM_ERROR = "unknown_platform_error"


class FacebookAPIError(Exception):
    """Structured error from a Facebook API call."""

    def __init__(
        self,
        code: FacebookErrorCode,
        message: str,
        http_status: int = 0,
        fb_error_code: Optional[int] = None,
        retryable: bool = False,
    ):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.fb_error_code = fb_error_code
        self.retryable = retryable
        super().__init__(message)


def classify_facebook_error(status_code: int, response_body: dict) -> FacebookAPIError:
    """
    Convert a Facebook Graph API error response into a structured FacebookAPIError.

    Never exposes tokens or sensitive internal data in error messages.
    """
    error_data = response_body.get("error", {})
    fb_code = error_data.get("code", 0)
    fb_subcode = error_data.get("error_subcode", 0)
    fb_type = error_data.get("type", "")
    fb_message = error_data.get("message", "")

    safe_message = _sanitize_message(fb_message)

    # OAuthException — authentication/permission errors
    if fb_type == "OAuthException":
        if fb_code == 190 or fb_subcode == 463:
            return FacebookAPIError(
                code=FacebookErrorCode.AUTHENTICATION_REQUIRED,
                message=f"Facebook authentication expired: {safe_message}",
                http_status=status_code,
                fb_error_code=fb_code,
                retryable=False,
            )
        if fb_code == 10 or "permission" in fb_message.lower():
            return FacebookAPIError(
                code=FacebookErrorCode.PERMISSION_DENIED,
                message=f"Insufficient permissions: {safe_message}",
                http_status=status_code,
                fb_error_code=fb_code,
                retryable=False,
            )
        return FacebookAPIError(
            code=FacebookErrorCode.AUTHENTICATION_REQUIRED,
            message=f"Authentication error: {safe_message}",
            http_status=status_code,
            fb_error_code=fb_code,
            retryable=False,
        )

    # Rate limiting
    if status_code == 429 or fb_code == 4 or fb_code == 32 or fb_code == 17:
        return FacebookAPIError(
            code=FacebookErrorCode.RATE_LIMITED,
            message="Facebook rate limit exceeded",
            http_status=status_code,
            fb_error_code=fb_code,
            retryable=True,
        )

    # Invalid content (400-level)
    if status_code == 400:
        lower_msg = fb_message.lower()
        if "photo" in lower_msg or "image" in lower_msg or "media" in lower_msg:
            return FacebookAPIError(
                code=FacebookErrorCode.INVALID_MEDIA,
                message=f"Invalid media: {safe_message}",
                http_status=status_code,
                fb_error_code=fb_code,
                retryable=False,
            )
        return FacebookAPIError(
            code=FacebookErrorCode.INVALID_CONTENT,
            message=f"Invalid content: {safe_message}",
            http_status=status_code,
            fb_error_code=fb_code,
            retryable=False,
        )

    # Permission denied
    if status_code == 403:
        return FacebookAPIError(
            code=FacebookErrorCode.PERMISSION_DENIED,
            message=f"Permission denied: {safe_message}",
            http_status=status_code,
            fb_error_code=fb_code,
            retryable=False,
        )

    # Server errors
    if status_code >= 500:
        return FacebookAPIError(
            code=FacebookErrorCode.PLATFORM_UNAVAILABLE,
            message="Facebook API temporarily unavailable",
            http_status=status_code,
            fb_error_code=fb_code,
            retryable=True,
        )

    return FacebookAPIError(
        code=FacebookErrorCode.UNKNOWN_PLATFORM_ERROR,
        message=f"Facebook API error ({status_code}): {safe_message}",
        http_status=status_code,
        fb_error_code=fb_code,
        retryable=status_code >= 500,
    )


def _sanitize_message(message: str) -> str:
    """Remove sensitive data from error messages."""
    sanitized = message[:300] if message else "No details"
    if "access_token" in sanitized.lower() or "bearer" in sanitized.lower():
        return "Authentication error (details redacted)"
    return sanitized
