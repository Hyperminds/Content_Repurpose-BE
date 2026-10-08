"""Domain models — data containers with no persistence or framework coupling."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.social_publishing.domain.enums import (
    PostStatus,
    SocialPlatform,
    AccountCapability,
    ConnectionStatus,
    ProviderType,
)


@dataclass
class SocialAccount:
    """A connected social media account belonging to a tenant."""

    id: str
    tenant_id: str
    platform: SocialPlatform
    account_name: str
    platform_account_id: str
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Connection status
    connection_status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    account_type: str = "personal"  # personal, business, page, organization

    # Provider metadata — HOW this account is published to. Defaults keep every
    # existing (pre-provider) account working as a native-API account.
    provider_type: ProviderType = ProviderType.NATIVE_API
    provider_name: str = ""  # concrete provider id; empty → derive from platform

    # Credential metadata (tokens stored separately for security).
    # These flags signal whether usable credentials exist without exposing values.
    has_access_token: bool = False
    token_expires_at: Optional[datetime] = None
    scopes: list[str] = field(default_factory=list)

    # Capabilities detected during connection
    capabilities: list[AccountCapability] = field(default_factory=list)


@dataclass
class OAuthState:
    """Represents a pending OAuth flow tied to a tenant and user."""

    state_token: str
    tenant_id: str
    user_id: str
    platform: SocialPlatform
    redirect_uri: str
    created_at: datetime
    expires_at: datetime
    # PKCE verifier (used by providers requiring OAuth 2.0 PKCE, e.g. X/Twitter).
    # Empty for providers that do not use PKCE. Never exposed to the client.
    code_verifier: str = ""


@dataclass
class ExternalSessionReference:
    """
    A reference to an isolated external browser/session (Phase 8).

    Trendzzo stores ONLY this reference — never raw cookies or session
    contents. `encrypted_reference` is an opaque, encrypted handle to session
    state that lives in an isolated store owned by the Hermes runtime. This
    object (and especially the encrypted reference) is never returned to the
    frontend.
    """

    id: str
    tenant_id: str
    provider: str          # ProviderName value (e.g. "hermes")
    platform: str          # SocialPlatform value
    account_id: str
    status: str = "active"  # active | expired | revoked
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_verified_at: Optional[datetime] = None
    # NOTE: encrypted_reference is intentionally NOT a field here — it stays in
    # the DB layer only, mirroring how encrypted_credentials never enter the
    # SocialAccount model. The repo returns this model without it.


@dataclass
class PendingConfirmation:
    """
    A user-assisted publish that is prepared and awaiting explicit confirmation.

    Persisted so the SAME operation (same operation_id) can be resumed when the
    user confirms, cancelled, or expired — never re-prepared with a new id
    (idempotency / duplicate prevention).

    Carries only non-sensitive fields. The browser session lives behind the
    Hermes profile key; no cookies/passwords are stored here.
    """

    id: str
    tenant_id: str
    account_id: str
    operation_id: str
    platform: str
    provider_name: str
    status: str  # action_required | waiting_for_user | published | cancelled | expired | failed | unknown
    # Prepared post preview (safe to show the user)
    title: str = ""
    body: str = ""
    subreddit: str = ""
    media_paths: list[str] = field(default_factory=list)
    # Browser session handle (opaque key, NOT a cookie/secret)
    session_profile_key: str = ""
    external_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


@dataclass
class SocialPost:
    """A piece of content to be published to one or more platforms."""

    id: str
    tenant_id: str
    account_id: str
    platform: SocialPlatform
    status: PostStatus
    content: str
    media_urls: list[str] = field(default_factory=list)
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 5
    platform_post_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class PublishingJob:
    """
    A record of a publishing attempt.

    Each time the system tries to publish a SocialPost, a job is created.
    This provides an audit trail of attempts, failures, and retries.
    """

    id: str
    tenant_id: str
    post_id: str
    platform: SocialPlatform
    status: PostStatus  # mirrors post status at time of job
    attempt_number: int = 1
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    platform_post_id: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class PublishingResult:
    """The outcome of a single publish attempt — returned by publishers."""

    success: bool
    platform_post_id: Optional[str] = None
    error_message: Optional[str] = None
    retryable: bool = True
