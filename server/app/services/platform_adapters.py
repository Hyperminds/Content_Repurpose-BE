"""
Platform adapters - modular publishing interface for each platform.
Each adapter handles: publish, validate, token refresh, and manual publish payload generation.

Currently all adapters simulate publishing (no real OAuth tokens yet).
When real API integrations are added, only the adapter internals change.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone


class BasePlatformAdapter(ABC):
    """Base class for all platform adapters."""

    platform_name: str = ""
    posting_mode: str = "auto"  # "auto" or "manual_assisted"

    @abstractmethod
    async def publish_post(self, user_id: str, content: str, media_urls: list = None) -> dict:
        """Publish content to the platform. Returns {success, platform_post_id, error}."""
        pass

    @abstractmethod
    def validate_content(self, content: str) -> dict:
        """Validate content against platform rules. Returns {valid, errors}."""
        pass

    async def refresh_token(self, user_id: str) -> dict:
        """Refresh OAuth token for the user. Override in subclasses."""
        return {"success": False, "error": "Token refresh not implemented"}

    def generate_manual_publish_payload(self, content: str, media_urls: list = None) -> dict:
        """Generate payload for manual publishing (copy/paste flow)."""
        return {
            "platform": self.platform_name,
            "content": content,
            "media_urls": media_urls or [],
            "instructions": f"Copy the content and paste it on {self.platform_name}.",
        }


# ============ LINKEDIN ADAPTER ============ #

class LinkedInAdapter(BasePlatformAdapter):
    platform_name = "linkedin"
    posting_mode = "auto"

    async def publish_post(self, user_id: str, content: str, media_urls: list = None) -> dict:
        # Use real LinkedIn API publishing
        from app.services.linkedin_service import publish_to_linkedin
        return await publish_to_linkedin(user_id, content, media_urls)

    def validate_content(self, content: str) -> dict:
        errors = []
        if len(content) > 3000:
            errors.append("Content exceeds 3000 character limit for LinkedIn")
        if not content.strip():
            errors.append("Content cannot be empty")
        return {"valid": len(errors) == 0, "errors": errors}


# ============ INSTAGRAM ADAPTER ============ #

class InstagramAdapter(BasePlatformAdapter):
    platform_name = "instagram"
    posting_mode = "auto"

    async def publish_post(self, user_id: str, content: str, media_urls: list = None) -> dict:
        # TODO: Implement real Instagram Graph API publishing
        return {
            "success": True,
            "platform_post_id": f"ig_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        }

    def validate_content(self, content: str) -> dict:
        errors = []
        if len(content) > 2200:
            errors.append("Caption exceeds 2200 character limit")
        hashtag_count = content.count("#")
        if hashtag_count > 30:
            errors.append("Too many hashtags (max 30)")
        return {"valid": len(errors) == 0, "errors": errors}


# ============ TWITTER/X ADAPTER ============ #

class TwitterAdapter(BasePlatformAdapter):
    platform_name = "twitter"
    posting_mode = "manual_assisted"

    async def publish_post(self, user_id: str, content: str, media_urls: list = None) -> dict:
        # Twitter/X is manual-assisted - generate payload for user
        return {
            "success": False,
            "error": "Twitter/X requires manual publishing. Content has been prepared for you.",
        }

    def validate_content(self, content: str) -> dict:
        errors = []
        # For threads, individual tweets should be under 280
        if len(content) > 280 and "\n\n" not in content:
            errors.append("Single tweet exceeds 280 characters. Consider using a thread format.")
        return {"valid": len(errors) == 0, "errors": errors}

    def generate_manual_publish_payload(self, content: str, media_urls: list = None) -> dict:
        return {
            "platform": "twitter",
            "content": content,
            "media_urls": media_urls or [],
            "platform_url": "https://twitter.com/compose/tweet",
            "instructions": "1. Click 'Open Twitter/X'\n2. Paste the content\n3. Add media if needed\n4. Post!",
        }


# ============ REDDIT ADAPTER ============ #

class RedditAdapter(BasePlatformAdapter):
    platform_name = "reddit"
    posting_mode = "auto"

    async def publish_post(self, user_id: str, content: str, media_urls: list = None) -> dict:
        from app.services.reddit_service import publish_to_reddit
        return await publish_to_reddit(user_id, content, media_urls)

    def validate_content(self, content: str) -> dict:
        from app.services.reddit_service import validate_reddit_content
        return validate_reddit_content(content)


# ============ MEDIUM ADAPTER ============ #

class MediumAdapter(BasePlatformAdapter):
    platform_name = "medium"
    posting_mode = "auto"

    async def publish_post(self, user_id: str, content: str, media_urls: list = None) -> dict:
        from app.services.medium_service import publish_to_medium
        return await publish_to_medium(user_id, content, media_urls)

    def validate_content(self, content: str) -> dict:
        from app.services.medium_service import validate_medium_content
        return validate_medium_content(content)


# ============ META/FACEBOOK ADAPTER ============ #

class MetaAdapter(BasePlatformAdapter):
    platform_name = "meta"
    posting_mode = "auto"

    async def publish_post(self, user_id: str, content: str, media_urls: list = None) -> dict:
        # TODO: Implement real Facebook Graph API publishing
        return {
            "success": True,
            "platform_post_id": f"fb_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        }

    def validate_content(self, content: str) -> dict:
        errors = []
        if len(content) > 63206:
            errors.append("Content exceeds Facebook's character limit")
        return {"valid": len(errors) == 0, "errors": errors}


# ============ QUORA ADAPTER ============ #

class QuoraAdapter(BasePlatformAdapter):
    platform_name = "quora"
    posting_mode = "manual_assisted"

    async def publish_post(self, user_id: str, content: str, media_urls: list = None) -> dict:
        # Quora is manual-assisted
        return {
            "success": False,
            "error": "Quora requires manual publishing. Content has been prepared for you.",
        }

    def validate_content(self, content: str) -> dict:
        errors = []
        if not content.strip():
            errors.append("Content cannot be empty")
        return {"valid": len(errors) == 0, "errors": errors}

    def generate_manual_publish_payload(self, content: str, media_urls: list = None) -> dict:
        return {
            "platform": "quora",
            "content": content,
            "media_urls": media_urls or [],
            "platform_url": "https://www.quora.com",
            "instructions": "1. Click 'Open Quora'\n2. Find a relevant question or create an answer\n3. Paste the content\n4. Submit!",
        }


# ============ ADAPTER REGISTRY ============ #

_adapters = {
    "linkedin": LinkedInAdapter(),
    "instagram": InstagramAdapter(),
    "twitter": TwitterAdapter(),
    "reddit": RedditAdapter(),
    "medium": MediumAdapter(),
    "meta": MetaAdapter(),
    "quora": QuoraAdapter(),
}


def get_adapter(platform: str) -> BasePlatformAdapter:
    """Get the adapter for a given platform."""
    adapter = _adapters.get(platform)
    if not adapter:
        raise ValueError(f"No adapter found for platform: {platform}")
    return adapter


def get_all_adapters() -> dict:
    """Get all registered adapters."""
    return _adapters
