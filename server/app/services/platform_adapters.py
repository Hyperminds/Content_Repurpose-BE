"""
Platform adapters - modular publishing interface for each platform.
Each adapter handles: publish, validate, token refresh, and manual publish payload generation.

In development mode (APP_ENV=development), all publish calls return mock results
without touching any real platform API.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from app.config import USE_MOCK
from app.mock_data.publishing import get_mock_publish_result


class BasePlatformAdapter(ABC):
    """Base class for all platform adapters."""

    platform_name: str = ""
    posting_mode: str = "auto"

    async def publish_post(self, user_id: str, content: str, media_urls: list = None) -> dict:
        """
        Publish content to the platform.
        In development mode, returns mock result without any real API call.
        """
        if USE_MOCK:
            # Check for error simulation flags
            from app.services.dev_simulator import get_simulation_flags
            flags = get_simulation_flags()
            return get_mock_publish_result(
                self.platform_name,
                simulate_failure=flags.get("simulate_api_failure", False),
                simulate_rate_limit=flags.get("simulate_rate_limit", False),
            )
        return await self._publish_post_real(user_id, content, media_urls)

    async def _publish_post_real(self, user_id: str, content: str, media_urls: list = None) -> dict:
        """Override in subclasses for real API publishing."""
        return {"success": False, "error": "Not implemented"}

    @abstractmethod
    def validate_content(self, content: str) -> dict:
        """Validate content against platform rules. Returns {valid, errors}."""
        pass

    async def refresh_token(self, user_id: str) -> dict:
        return {"success": False, "error": "Token refresh not implemented"}

    def generate_manual_publish_payload(self, content: str, media_urls: list = None) -> dict:
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

    async def _publish_post_real(self, user_id: str, content: str, media_urls: list = None) -> dict:
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

    async def _publish_post_real(self, user_id: str, content: str, media_urls: list = None) -> dict:
        return {"success": True, "platform_post_id": f"ig_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"}

    def validate_content(self, content: str) -> dict:
        errors = []
        if len(content) > 2200:
            errors.append("Caption exceeds 2200 character limit")
        if content.count("#") > 30:
            errors.append("Too many hashtags (max 30)")
        return {"valid": len(errors) == 0, "errors": errors}


class TwitterAdapter(BasePlatformAdapter):
    platform_name = "twitter"
    posting_mode = "manual_assisted"

    async def _publish_post_real(self, user_id: str, content: str, media_urls: list = None) -> dict:
        return {"success": False, "error": "Twitter/X requires manual publishing."}

    def validate_content(self, content: str) -> dict:
        errors = []
        if len(content) > 280 and "\n\n" not in content:
            errors.append("Single tweet exceeds 280 characters.")
        return {"valid": len(errors) == 0, "errors": errors}

    def generate_manual_publish_payload(self, content: str, media_urls: list = None) -> dict:
        return {"platform": "twitter", "content": content, "media_urls": media_urls or [], "platform_url": "https://twitter.com/compose/tweet", "instructions": "1. Click 'Open Twitter/X'\n2. Paste the content\n3. Post!"}


class RedditAdapter(BasePlatformAdapter):
    platform_name = "reddit"
    posting_mode = "auto"

    async def _publish_post_real(self, user_id: str, content: str, media_urls: list = None) -> dict:
        from app.services.reddit_service import publish_to_reddit
        return await publish_to_reddit(user_id, content, media_urls)

    def validate_content(self, content: str) -> dict:
        from app.services.reddit_service import validate_reddit_content
        return validate_reddit_content(content)


class MediumAdapter(BasePlatformAdapter):
    platform_name = "medium"
    posting_mode = "auto"

    async def _publish_post_real(self, user_id: str, content: str, media_urls: list = None) -> dict:
        from app.services.medium_service import publish_to_medium
        return await publish_to_medium(user_id, content, media_urls)

    def validate_content(self, content: str) -> dict:
        from app.services.medium_service import validate_medium_content
        return validate_medium_content(content)


class MetaAdapter(BasePlatformAdapter):
    platform_name = "meta"
    posting_mode = "auto"

    async def _publish_post_real(self, user_id: str, content: str, media_urls: list = None) -> dict:
        return {"success": True, "platform_post_id": f"fb_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"}

    def validate_content(self, content: str) -> dict:
        errors = []
        if len(content) > 63206:
            errors.append("Content exceeds Facebook's character limit")
        return {"valid": len(errors) == 0, "errors": errors}


class QuoraAdapter(BasePlatformAdapter):
    platform_name = "quora"
    posting_mode = "manual_assisted"

    async def _publish_post_real(self, user_id: str, content: str, media_urls: list = None) -> dict:
        return {"success": False, "error": "Quora requires manual publishing."}

    def validate_content(self, content: str) -> dict:
        errors = []
        if not content.strip():
            errors.append("Content cannot be empty")
        return {"valid": len(errors) == 0, "errors": errors}

    def generate_manual_publish_payload(self, content: str, media_urls: list = None) -> dict:
        return {"platform": "quora", "content": content, "media_urls": media_urls or [], "platform_url": "https://www.quora.com", "instructions": "1. Open Quora\n2. Find a relevant question\n3. Paste the content\n4. Submit!"}


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
