"""Publisher registry — runtime lookup of platform publishers.

Usage:
    registry = PublisherRegistry()
    registry.register(linkedin_publisher)
    publisher = registry.get("linkedin")
    result = await publisher.publish(post, token)

The orchestrator uses the registry to find the correct publisher for a post's
platform. This decouples the orchestrator from any platform-specific code.
"""

from typing import Optional

from app.social_publishing.domain.enums import SocialPlatform
from app.social_publishing.domain.exceptions import PlatformNotSupported
from app.social_publishing.publishers.base import SocialPublisher


class PublisherRegistry:
    """Thread-safe registry mapping platform names to publisher instances."""

    def __init__(self) -> None:
        self._publishers: dict[str, SocialPublisher] = {}

    def register(self, publisher: SocialPublisher) -> None:
        """Register a publisher for its platform."""
        self._publishers[publisher.platform_name] = publisher

    def get(self, platform: str | SocialPlatform) -> SocialPublisher:
        """
        Get the publisher for a platform.

        Raises PlatformNotSupported if no publisher is registered.
        """
        key = platform.value if isinstance(platform, SocialPlatform) else platform
        publisher = self._publishers.get(key)
        if publisher is None:
            raise PlatformNotSupported(key)
        return publisher

    def get_optional(self, platform: str | SocialPlatform) -> Optional[SocialPublisher]:
        """Get the publisher for a platform, or None if not registered."""
        key = platform.value if isinstance(platform, SocialPlatform) else platform
        return self._publishers.get(key)

    def has(self, platform: str | SocialPlatform) -> bool:
        """Check if a publisher is registered for the platform."""
        key = platform.value if isinstance(platform, SocialPlatform) else platform
        return key in self._publishers

    @property
    def registered_platforms(self) -> list[str]:
        """List all registered platform names."""
        return list(self._publishers.keys())
