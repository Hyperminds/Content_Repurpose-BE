"""Instagram integration — auth provider, publisher, media validation, error mapping.

All Instagram-specific logic is isolated in this package. The rest of the
social publishing system interacts with Instagram only through:
  - InstagramAuthProvider (implements SocialAuthProvider protocol)
  - InstagramPublisher (implements SocialPublisher protocol)
"""

from app.social_publishing.integrations.instagram.auth_provider import InstagramAuthProvider
from app.social_publishing.integrations.instagram.publisher import InstagramPublisher

__all__ = ["InstagramAuthProvider", "InstagramPublisher"]
