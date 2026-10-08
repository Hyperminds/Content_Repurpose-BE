"""Base publisher protocol.

Every platform publisher (Instagram, LinkedIn, etc.) must implement this
interface. The orchestrator calls `publish()` without knowing which platform
is behind it.

Using Protocol (structural subtyping) instead of ABC so publishers don't need
to explicitly inherit — they just need to match the signature. This makes
testing trivial (any object with a matching `publish` method works).
"""

from typing import Protocol, runtime_checkable

from app.social_publishing.domain.models import SocialPost, PublishingResult


@runtime_checkable
class SocialPublisher(Protocol):
    """Protocol that all platform publishers must satisfy."""

    @property
    def platform_name(self) -> str:
        """The platform identifier (e.g. 'linkedin', 'instagram')."""
        ...

    async def publish(self, post: SocialPost, access_token: str) -> PublishingResult:
        """
        Publish a post to the platform.

        Args:
            post: The social post to publish (contains content, media_urls, etc.)
            access_token: The OAuth access token for the target account.

        Returns:
            A PublishingResult indicating success/failure and platform post id.
        """
        ...

    async def validate_content(self, post: SocialPost) -> list[str]:
        """
        Validate that the post content meets platform requirements.

        Returns a list of validation error messages (empty = valid).
        """
        ...
