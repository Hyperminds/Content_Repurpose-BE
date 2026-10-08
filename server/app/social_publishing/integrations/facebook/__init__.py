"""Facebook integration — auth provider and publisher for Facebook Pages.

IMPORTANT: Facebook only supports publishing to Pages via the API.
Personal profile publishing and Group publishing are NOT supported.

All Facebook-specific logic is isolated in this package.
"""

from app.social_publishing.integrations.facebook.auth_provider import FacebookAuthProvider
from app.social_publishing.integrations.facebook.publisher import FacebookPublisher

__all__ = ["FacebookAuthProvider", "FacebookPublisher"]
