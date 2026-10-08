"""Facebook publisher — publishes posts to Facebook Pages via the Graph API.

Implements the SocialPublisher protocol. Supports:
  - Text posts (message only)
  - Link posts (message + URL)
  - Photo posts (image URL)

IMPORTANT: Only publishes to Facebook Pages. Personal profile publishing
is not supported by the Facebook API.

The publisher is stateless. It receives a Page access token and a post,
then calls the appropriate Graph API endpoint.
"""

import httpx
from typing import Optional

from app.social_publishing.domain.enums import SocialPlatform
from app.social_publishing.domain.models import PublishingResult, SocialPost
from app.social_publishing.integrations.facebook.constants import (
    GRAPH_API_BASE,
    HTTP_TIMEOUT,
    UPLOAD_TIMEOUT,
)
from app.social_publishing.integrations.facebook.errors import (
    classify_facebook_error,
    FacebookAPIError,
)
from app.social_publishing.integrations.facebook.media_validation import (
    detect_post_type,
    validate_for_publishing,
)


class FacebookPublisher:
    """Publishes content to Facebook Pages via the Graph API."""

    @property
    def platform_name(self) -> str:
        return SocialPlatform.META.value

    async def publish(self, post: SocialPost, access_token: str) -> PublishingResult:
        """
        Publish a social post to a Facebook Page.

        The access_token must be a valid Page access token.
        The post.account_id is the Facebook Page ID.
        """
        # Validate content
        validation_errors = await self.validate_content(post)
        if validation_errors:
            return PublishingResult(
                success=False,
                error_message=f"Validation failed: {'; '.join(validation_errors)}",
                retryable=False,
            )

        page_id = post.account_id
        post_type = detect_post_type(post.content, post.media_urls)

        try:
            if post_type == "photo":
                post_id = await self._publish_photo(
                    page_id, access_token, post.content, post.media_urls[0]
                )
            elif post_type == "link":
                # Extract link from content if present (first URL in media_urls or content)
                link_url = self._extract_link(post)
                post_id = await self._publish_link(
                    page_id, access_token, post.content, link_url
                )
            else:
                post_id = await self._publish_text(
                    page_id, access_token, post.content
                )

            return PublishingResult(
                success=True,
                platform_post_id=post_id,
            )
        except FacebookAPIError as e:
            return PublishingResult(
                success=False,
                error_message=e.message,
                retryable=e.retryable,
            )

    async def validate_content(self, post: SocialPost) -> list[str]:
        """Validate post content meets Facebook Page requirements."""
        return validate_for_publishing(post.content, post.media_urls)

    # ── Text post ─────────────────────────────────────────────────────────────

    async def _publish_text(
        self, page_id: str, access_token: str, message: str
    ) -> str:
        """Publish a text-only post to a Page."""
        url = f"{GRAPH_API_BASE}/{page_id}/feed"
        payload = {"message": message, "access_token": access_token}

        return await self._post_to_graph(url, payload)

    # ── Link post ─────────────────────────────────────────────────────────────

    async def _publish_link(
        self, page_id: str, access_token: str, message: str, link: str
    ) -> str:
        """Publish a link post to a Page."""
        url = f"{GRAPH_API_BASE}/{page_id}/feed"
        payload = {
            "message": message,
            "link": link,
            "access_token": access_token,
        }

        return await self._post_to_graph(url, payload)

    # ── Photo post ────────────────────────────────────────────────────────────

    async def _publish_photo(
        self, page_id: str, access_token: str, caption: str, image_url: str
    ) -> str:
        """Publish a photo post to a Page using an image URL."""
        url = f"{GRAPH_API_BASE}/{page_id}/photos"
        payload = {
            "url": image_url,
            "caption": caption,
            "access_token": access_token,
        }

        return await self._post_to_graph(url, payload, timeout=UPLOAD_TIMEOUT)

    # ── Graph API call ────────────────────────────────────────────────────────

    async def _post_to_graph(
        self, url: str, payload: dict, timeout: float = HTTP_TIMEOUT
    ) -> str:
        """
        Make a POST request to the Graph API.

        Returns the post ID on success.
        Raises FacebookAPIError on failure.
        """
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, data=payload)

        if response.status_code == 200:
            data = response.json()
            # Different endpoints return id differently
            return data.get("post_id") or data.get("id", "")

        # Error handling
        try:
            error_body = response.json()
        except Exception:
            error_body = {"error": {"message": response.text[:300]}}

        raise classify_facebook_error(response.status_code, error_body)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_link(self, post: SocialPost) -> str:
        """Extract a link URL from post content or media_urls."""
        # Check media_urls for a non-image URL
        for url in post.media_urls:
            lower = url.lower()
            image_exts = (".jpg", ".jpeg", ".png", ".gif")
            video_exts = (".mp4", ".mov", ".avi")
            if not any(lower.endswith(ext) for ext in image_exts + video_exts):
                return url

        # Fallback: look for URLs in content text
        import re
        url_pattern = r'https?://[^\s<>\"\']+' 
        matches = re.findall(url_pattern, post.content)
        return matches[0] if matches else ""
