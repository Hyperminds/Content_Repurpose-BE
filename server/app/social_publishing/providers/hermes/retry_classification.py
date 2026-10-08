"""Hermes retry classification (Phase 14).

External/browser publishing has different retry semantics from native APIs, so
Trendzzo must NOT apply native API retry rules to Hermes results. This module
classifies a provider-neutral ExternalErrorCode into one of three dispositions:

  RETRYABLE      — transient; safe to retry (network blip, browser crash before
                   publish, temporary page-load failure, provider unavailable)
  NON_RETRYABLE  — permanent; retrying cannot help (account disconnected, auth
                   required, unsupported platform, automation not permitted,
                   invalid content, verification failed)
  VERIFY_FIRST   — uncertain; the post may or may not exist. NEVER blindly
                   retry — verify first (EXTERNAL_PUBLISH_UNKNOWN).

ACTION_REQUIRED is not a retry disposition — it pauses for the user.
"""

from enum import Enum
from typing import Optional

from app.social_publishing.providers.errors import ExternalErrorCode


class RetryDisposition(str, Enum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    VERIFY_FIRST = "verify_first"
    USER_ACTION = "user_action"


_RETRYABLE = {
    ExternalErrorCode.EXTERNAL_PROVIDER_UNAVAILABLE,
    ExternalErrorCode.EXTERNAL_PUBLISH_TIMEOUT,
    ExternalErrorCode.EXTERNAL_MEDIA_UPLOAD_FAILED,
}

_NON_RETRYABLE = {
    ExternalErrorCode.EXTERNAL_AUTH_REQUIRED,
    ExternalErrorCode.EXTERNAL_ACCOUNT_DISCONNECTED,
    ExternalErrorCode.EXTERNAL_AUTOMATION_NOT_PERMITTED,
    ExternalErrorCode.EXTERNAL_PLATFORM_UNSUPPORTED,
    ExternalErrorCode.EXTERNAL_CONTENT_REJECTED,
    ExternalErrorCode.EXTERNAL_VERIFICATION_FAILED,
}

_VERIFY_FIRST = {
    ExternalErrorCode.EXTERNAL_PUBLISH_UNKNOWN,
}

_USER_ACTION = {
    ExternalErrorCode.ACTION_REQUIRED,
}


def classify(code: Optional[ExternalErrorCode]) -> RetryDisposition:
    """
    Map a provider-neutral error code to a retry disposition.

    Unknown/None codes are treated as VERIFY_FIRST (fail safe): we never assume
    a browser action definitively failed without confirming it, to avoid
    creating a duplicate post.
    """
    if code is None:
        return RetryDisposition.VERIFY_FIRST
    if code in _RETRYABLE:
        return RetryDisposition.RETRYABLE
    if code in _NON_RETRYABLE:
        return RetryDisposition.NON_RETRYABLE
    if code in _VERIFY_FIRST:
        return RetryDisposition.VERIFY_FIRST
    if code in _USER_ACTION:
        return RetryDisposition.USER_ACTION
    # Default: be cautious — verify rather than blind-retry.
    return RetryDisposition.VERIFY_FIRST
