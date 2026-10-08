"""Enumerations for the social publishing domain."""

from enum import Enum


class PostStatus(str, Enum):
    """Lifecycle states for a social post."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"

    # ── Hybrid-publishing lifecycle states (Phase 10) ─────────────────────────
    # Used by external/user-assisted providers. Native API publishing continues
    # to use the classic states above unchanged.
    VERIFYING = "verifying"                # Confirming an uncertain outcome
    ACTION_REQUIRED = "action_required"    # Provider needs a manual user step
    WAITING_FOR_USER = "waiting_for_user"  # Paused pending user interaction
    UNKNOWN = "unknown"                    # Outcome uncertain — MUST be verified

    @classmethod
    def terminal_states(cls) -> set["PostStatus"]:
        """States from which no further transitions are allowed."""
        return {cls.PUBLISHED, cls.CANCELLED}

    @classmethod
    def active_states(cls) -> set["PostStatus"]:
        """States that indicate in-flight processing."""
        return {
            cls.QUEUED, cls.PUBLISHING, cls.RETRYING,
            cls.VERIFYING, cls.ACTION_REQUIRED, cls.WAITING_FOR_USER,
        }

    @classmethod
    def needs_user_action(cls) -> set["PostStatus"]:
        """States surfaced to the user as 'action required' rather than failure."""
        return {cls.ACTION_REQUIRED, cls.WAITING_FOR_USER}


# Valid state transitions: {from_status: set(allowed_to_statuses)}
#
# Design for the hybrid lifecycle:
#   PUBLISHING → VERIFYING → PUBLISHED / FAILED / UNKNOWN
#   PUBLISHING → ACTION_REQUIRED → WAITING_FOR_USER → PUBLISHING  (user-assisted)
#   UNKNOWN    → VERIFYING only  (NEVER directly to QUEUED/RETRYING — must be
#                                 verified before any republish decision)
VALID_TRANSITIONS: dict[PostStatus, set[PostStatus]] = {
    PostStatus.DRAFT: {PostStatus.SCHEDULED, PostStatus.CANCELLED},
    PostStatus.SCHEDULED: {PostStatus.QUEUED, PostStatus.CANCELLED, PostStatus.DRAFT},
    PostStatus.QUEUED: {PostStatus.PUBLISHING, PostStatus.CANCELLED, PostStatus.FAILED},
    PostStatus.PUBLISHING: {
        PostStatus.PUBLISHED, PostStatus.FAILED, PostStatus.RETRYING,
        PostStatus.VERIFYING, PostStatus.ACTION_REQUIRED, PostStatus.UNKNOWN,
    },
    PostStatus.FAILED: {PostStatus.RETRYING, PostStatus.CANCELLED, PostStatus.SCHEDULED},
    PostStatus.RETRYING: {PostStatus.QUEUED, PostStatus.FAILED, PostStatus.CANCELLED},
    PostStatus.PUBLISHED: set(),
    PostStatus.CANCELLED: set(),

    # Verification resolves to a definite outcome or stays UNKNOWN.
    PostStatus.VERIFYING: {PostStatus.PUBLISHED, PostStatus.FAILED, PostStatus.UNKNOWN},
    # User-assisted pause/resume flow.
    PostStatus.ACTION_REQUIRED: {
        PostStatus.WAITING_FOR_USER, PostStatus.CANCELLED, PostStatus.FAILED,
    },
    PostStatus.WAITING_FOR_USER: {
        PostStatus.PUBLISHING, PostStatus.CANCELLED, PostStatus.FAILED,
    },
    # UNKNOWN must be VERIFIED — it can never transition straight to a retry.
    PostStatus.UNKNOWN: {PostStatus.VERIFYING, PostStatus.CANCELLED},
}


def can_transition(from_status: PostStatus, to_status: PostStatus) -> bool:
    """True if moving from one status to another is allowed."""
    return to_status in VALID_TRANSITIONS.get(from_status, set())


class SocialPlatform(str, Enum):
    """Supported social media platforms."""

    LINKEDIN = "linkedin"
    INSTAGRAM = "instagram"
    TWITTER = "twitter"
    REDDIT = "reddit"
    MEDIUM = "medium"
    META = "meta"
    # Facebook as a distinct platform value used by the user-assisted Hermes
    # workflow (profile/page browser posting). The native Meta Graph API path
    # continues to use META and is unaffected by this value.
    FACEBOOK = "facebook"
    THREADS = "threads"
    QUORA = "quora"


class AccountCapability(str, Enum):
    """Capabilities a connected account may support."""

    TEXT_PUBLISHING = "text_publishing"
    IMAGE_PUBLISHING = "image_publishing"
    VIDEO_PUBLISHING = "video_publishing"
    CAROUSEL_PUBLISHING = "carousel_publishing"
    STORY_PUBLISHING = "story_publishing"
    ORGANIZATION_PUBLISHING = "organization_publishing"
    SCHEDULED_PUBLISHING = "scheduled_publishing"
    ANALYTICS = "analytics"
    COMMENT_MANAGEMENT = "comment_management"


class ConnectionStatus(str, Enum):
    """Status of a social account connection."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    REAUTH_REQUIRED = "reauth_required"
    TOKEN_EXPIRED = "token_expired"
    PENDING = "pending"


class ProviderType(str, Enum):
    """
    How a connected account's platform execution is performed.

    Trendzzo remains the control plane; a provider type only describes the
    *execution method* a publishing provider uses. It never changes who owns
    scheduling, jobs, tenancy, or state (that is always Trendzzo).
    """

    NATIVE_API = "native_api"                # Official platform API / OAuth
    EXTERNAL_AGENT = "external_agent"         # Hermes browser agent (no user interaction)
    USER_ASSISTED_AGENT = "user_assisted_agent"  # Hermes with required manual steps
    BYOK_API = "byok_api"                     # Customer-supplied API credentials


class ProviderName(str, Enum):
    """
    Stable identifiers for the concrete provider that executes a platform.

    Distinct from SocialPlatform: several platforms can share one provider
    (e.g. Instagram + Facebook + Threads all use the Meta provider), and one
    platform may have multiple providers (e.g. X native vs BYOK X).
    """

    LINKEDIN = "linkedin"
    META = "meta"
    X = "x"
    HERMES = "hermes"
    BYOK_X = "byok_x"


# Default native provider mapping for platforms that ship with an official
# integration. Used to backfill provider metadata for accounts connected
# before provider fields existed. Anything not listed defaults to the platform
# value itself with NATIVE_API (safe, non-breaking).
DEFAULT_NATIVE_PROVIDER: dict[SocialPlatform, ProviderName] = {
    SocialPlatform.LINKEDIN: ProviderName.LINKEDIN,
    SocialPlatform.META: ProviderName.META,
    SocialPlatform.INSTAGRAM: ProviderName.META,
    SocialPlatform.THREADS: ProviderName.META,
    SocialPlatform.TWITTER: ProviderName.X,
}

