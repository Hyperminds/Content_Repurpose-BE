"""Provider-neutral error codes and normalized results (Phase 15).

Every provider — native API, Hermes external agent, user-assisted, BYOK —
returns results using these shared, platform-agnostic codes. The publishing
engine and UI reason about these codes without knowing which provider or
platform produced them.

Raw provider/browser stack traces must NEVER be surfaced to users. Messages
are sanitized before they leave a provider.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ExternalErrorCode(str, Enum):
    """Provider-neutral outcome/error codes."""

    # Availability / infra
    EXTERNAL_PROVIDER_UNAVAILABLE = "external_provider_unavailable"
    EXTERNAL_PUBLISH_TIMEOUT = "external_publish_timeout"

    # Auth / account
    EXTERNAL_AUTH_REQUIRED = "external_auth_required"
    EXTERNAL_ACCOUNT_DISCONNECTED = "external_account_disconnected"

    # Policy / support
    EXTERNAL_AUTOMATION_NOT_PERMITTED = "external_automation_not_permitted"
    EXTERNAL_PLATFORM_UNSUPPORTED = "external_platform_unsupported"

    # Content / media
    EXTERNAL_CONTENT_REJECTED = "external_content_rejected"
    EXTERNAL_MEDIA_UPLOAD_FAILED = "external_media_upload_failed"

    # Verification / uncertainty
    EXTERNAL_PUBLISH_UNKNOWN = "external_publish_unknown"
    EXTERNAL_VERIFICATION_FAILED = "external_verification_failed"

    # Interaction required (not a hard failure)
    ACTION_REQUIRED = "action_required"


class ExternalProviderError(Exception):
    """Structured, provider-neutral error raised by a publishing provider."""

    def __init__(
        self,
        code: ExternalErrorCode,
        message: str,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = _sanitize(message)
        self.retryable = retryable
        super().__init__(self.message)


class ExternalResultStatus(str, Enum):
    """Normalized status returned by a provider execution."""

    PUBLISHED = "published"
    FAILED = "failed"
    ACTION_REQUIRED = "action_required"
    UNKNOWN = "unknown"


@dataclass
class PublishInstruction:
    """
    Normalized, provider-agnostic publishing instruction.

    This is what Trendzzo hands to ANY provider. It carries an operation_id for
    idempotency and only the data needed to execute one publish. It never
    carries credentials — providers resolve those out of band via the vault /
    session reference, scoped to the account.
    """

    operation_id: str
    tenant_id: str
    account_id: str
    platform: str
    provider_name: str
    text: str = ""
    media_urls: list[str] = field(default_factory=list)
    # Opaque, non-secret metadata (e.g. content_version) for tracing.
    meta: dict = field(default_factory=dict)


@dataclass
class ProviderResult:
    """
    Normalized result returned by every provider.

    `status` drives the engine's lifecycle decisions. UNKNOWN must trigger
    verification, never a blind retry. ACTION_REQUIRED pauses for the user.
    """

    success: bool
    status: ExternalResultStatus
    external_post_id: Optional[str] = None
    external_url: Optional[str] = None
    error_code: Optional[ExternalErrorCode] = None
    error_message: Optional[str] = None
    retryable: bool = False

    def sanitized(self) -> "ProviderResult":
        """Return a copy with the error message sanitized (defensive)."""
        if self.error_message:
            self.error_message = _sanitize(self.error_message)
        return self


# ── Secret sanitization ─────────────────────────────────────────────────────

_SECRET_MARKERS = (
    "access_token",
    "refresh_token",
    "bearer ",
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "client_secret",
    "session",
    "api_key",
    "apikey",
)


def _sanitize(message: Optional[str]) -> str:
    """
    Strip anything that looks like a secret from an error message and cap
    length. Never lets raw provider/browser internals through verbatim.
    """
    if not message:
        return "No details"
    text = str(message)[:300]
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return "Provider error (sensitive details redacted)"
    return text
