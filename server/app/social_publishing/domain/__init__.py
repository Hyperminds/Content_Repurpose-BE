"""Domain models — pure Python types with no database or framework dependencies."""

from app.social_publishing.domain.enums import (
    PostStatus,
    SocialPlatform,
    AccountCapability,
    ConnectionStatus,
)
from app.social_publishing.domain.models import (
    SocialAccount,
    SocialPost,
    PublishingJob,
    PublishingResult,
    OAuthState,
)
from app.social_publishing.domain.exceptions import (
    SocialPublishingError,
    TenantAccessDenied,
    InvalidStateTransition,
    PostNotFound,
    AccountNotFound,
    PlatformNotSupported,
    SchedulingError,
    PublishingFailed,
    ValidationError,
)

__all__ = [
    "PostStatus",
    "SocialPlatform",
    "AccountCapability",
    "ConnectionStatus",
    "SocialAccount",
    "SocialPost",
    "PublishingJob",
    "PublishingResult",
    "OAuthState",
    "SocialPublishingError",
    "TenantAccessDenied",
    "InvalidStateTransition",
    "PostNotFound",
    "AccountNotFound",
    "PlatformNotSupported",
    "SchedulingError",
    "PublishingFailed",
    "ValidationError",
]
