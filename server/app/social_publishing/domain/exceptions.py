"""Application-level exceptions for the social publishing module.

These are raised by services and caught by the API layer, which maps them to
HTTP status codes. They never expose internal implementation details.
"""


class SocialPublishingError(Exception):
    """Base exception for all social publishing errors."""

    def __init__(self, message: str = "An error occurred in the publishing system"):
        self.message = message
        super().__init__(self.message)


class TenantAccessDenied(SocialPublishingError):
    """Raised when a tenant attempts to access another tenant's resources."""

    def __init__(self, resource_type: str = "resource"):
        super().__init__(f"Access denied: {resource_type} does not belong to this tenant")


class InvalidStateTransition(SocialPublishingError):
    """Raised when a post cannot transition from its current state."""

    def __init__(self, current_status: str, target_status: str):
        super().__init__(
            f"Cannot transition from '{current_status}' to '{target_status}'"
        )


class PostNotFound(SocialPublishingError):
    """Raised when a post is not found."""

    def __init__(self, post_id: str = ""):
        super().__init__(f"Post not found: {post_id}" if post_id else "Post not found")


class AccountNotFound(SocialPublishingError):
    """Raised when a social account is not found."""

    def __init__(self, account_id: str = ""):
        super().__init__(
            f"Social account not found: {account_id}" if account_id else "Social account not found"
        )


class PlatformNotSupported(SocialPublishingError):
    """Raised when a platform is not supported or has no registered publisher."""

    def __init__(self, platform: str = ""):
        super().__init__(
            f"Platform not supported: {platform}" if platform else "Platform not supported"
        )


class SchedulingError(SocialPublishingError):
    """Raised when a post cannot be scheduled (e.g., time in the past)."""

    def __init__(self, reason: str = "Invalid scheduling parameters"):
        super().__init__(reason)


class PublishingFailed(SocialPublishingError):
    """Raised when a publishing attempt fails irrecoverably."""

    def __init__(self, reason: str = "Publishing failed"):
        super().__init__(reason)


class ValidationError(SocialPublishingError):
    """Raised when request data fails validation."""

    def __init__(self, reason: str = "Validation failed"):
        super().__init__(reason)
