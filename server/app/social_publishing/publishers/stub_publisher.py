"""Stub publisher for testing and development.

Always succeeds (or can be configured to fail). Useful for:
  - Unit tests that need a publisher without real API calls
  - Development mode (mock data)
  - Integration testing the orchestrator
"""

from app.social_publishing.domain.models import SocialPost, PublishingResult


class StubPublisher:
    """A no-op publisher that simulates success or failure."""

    def __init__(
        self,
        platform: str = "stub",
        should_fail: bool = False,
        error_message: str = "Simulated failure",
        retryable: bool = True,
    ) -> None:
        self._platform = platform
        self._should_fail = should_fail
        self._error_message = error_message
        self._retryable = retryable
        self.publish_calls: list[SocialPost] = []

    @property
    def platform_name(self) -> str:
        return self._platform

    async def publish(self, post: SocialPost, access_token: str) -> PublishingResult:
        """Simulate a publish attempt."""
        self.publish_calls.append(post)

        if self._should_fail:
            return PublishingResult(
                success=False,
                error_message=self._error_message,
                retryable=self._retryable,
            )

        return PublishingResult(
            success=True,
            platform_post_id=f"stub_{post.id}_{len(self.publish_calls)}",
        )

    async def validate_content(self, post: SocialPost) -> list[str]:
        """Stub validation — always passes."""
        errors: list[str] = []
        if not post.content.strip():
            errors.append("Content cannot be empty")
        return errors
