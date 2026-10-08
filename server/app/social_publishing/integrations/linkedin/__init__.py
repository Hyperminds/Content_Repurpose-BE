"""LinkedIn integration — auth provider, publisher, and error mapping.

All LinkedIn-specific logic is isolated in this package. The rest of the
social publishing system interacts with LinkedIn only through:
  - LinkedInAuthProvider (implements SocialAuthProvider protocol)
  - LinkedInPublisher (implements SocialPublisher protocol)
"""

from app.social_publishing.integrations.linkedin.publisher import LinkedInPublisher

__all__ = ["LinkedInPublisher"]
