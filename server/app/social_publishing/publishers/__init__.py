"""Publisher abstraction layer.

Defines the interface that all platform publishers must implement, plus a
registry for runtime lookup. No platform-specific code lives here.
"""

from app.social_publishing.publishers.base import SocialPublisher
from app.social_publishing.publishers.registry import PublisherRegistry
from app.social_publishing.publishers.stub_publisher import StubPublisher

__all__ = ["SocialPublisher", "PublisherRegistry", "StubPublisher"]
