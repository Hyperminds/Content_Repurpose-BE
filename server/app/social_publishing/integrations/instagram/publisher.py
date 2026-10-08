"""Instagram publisher — publishes posts via the Instagram Content Publishing API.

Implements the SocialPublisher protocol using Instagram's two-step container workflow:
  1. Create a media container (POST /{ig_user_id}/media)
  2. Publish the container (POST /{ig_user_id}/media_publish)

For videos/reels, an additional polling step checks container status before publishing.

IMPORTANT: Only publishes to Instagram Professional accounts (Business/Creator).
Personal accounts cannot publish via the API.
"""

import asyncio
from typing import Optional

import httpx

from app.social_publishing.domain.enums import SocialPlatform
from app.social_publishing.domain.models import PublishingResult, SocialPost
from app.social_publishing.integrations.instagram.constants import (
    API_VERSION,
    CONTAINER_POLL_INTERVAL,
    CONTAINER_POLL_MAX_WAIT,
    GRAPH_API_HOST,
    HTTP_TIMEOUT,
    MAX_CAPTION_LENGTH,
)
from app.social_publishing.integrations.instagram.errors import (
    InstagramAPIError,
    InstagramErrorCode,
    classify_meta_error,
)
from app.social_publishing.integrations.instagram.media_validation import (
    detect_media_type,
    validate_for_publishing,
)
from app.social_publishing.integrations.url_safety import is_safe_url_async


class InstagramPublisher:
    """Publishes content to Instagram via the Content Publishing API."""

    @property
    def platform_name(self) -> str:
        return SocialPlatform.INSTAGRAM.value

    async def publish(self, post: SocialPost, access_token: str) -> PublishingResult:
        """
        Publish a social post to Instagram.

        The post must include at least one media URL (Instagram requires media).
        The post.account_id is the Instagram Professional account user ID.
        """
        # Validate content
        validation_errors = await self.validate_content(post)
        if validation_errors:
            return PublishingResult(
                success=False,
                error_message=f"Validation failed: {'; '.join(validation_errors)}",
                retryable=False,
            )

        ig_user_id = post.account_id
        media_type = detect_media_type(post.media_urls)
        caption = post.content[:MAX_CAPTION_LENGTH] if post.content else ""

        try:
            if media_type == "video":
                media_id = await self._publish_video(
                    ig_user_id, access_token, post.media_urls[0], caption
                )
            elif media_type == "carousel" and len(post.media_urls) > 1:
                media_id = await self._publish_carousel(
                    ig_user_id, access_token, post.media_urls, caption
                )
            else:
                media_id = await self._publish_image(
                    ig_user_id, access_token, post.media_urls[0], caption
                )

            return PublishingResult(success=True, platform_post_id=media_id)

        except InstagramAPIError as e:
            return PublishingResult(
                success=False,
                error_message=e.message,
                retryable=e.retryable,
            )

    async def validate_content(self, post: SocialPost) -> list[str]:
        """Validate post content meets Instagram requirements."""
        media_type = detect_media_type(post.media_urls)
        return validate_for_publishing(post.content, post.media_urls, media_type)

    # ── Image publishing ──────────────────────────────────────────────────────

    async def _publish_image(
        self, ig_user_id: str, access_token: str, image_url: str, caption: str
    ) -> str:
        """Publish a single image post (container → publish)."""
        if not await is_safe_url_async(image_url):
            raise InstagramAPIError(
                code=InstagramErrorCode.INVALID_MEDIA, message="Unsafe media URL", retryable=False
            )

        # Step 1: Create container
        container_id = await self._create_image_container(
            ig_user_id, access_token, image_url, caption
        )

        # Step 2: Publish
        return await self._publish_container(ig_user_id, access_token, container_id)

    # ── Video / Reels publishing ──────────────────────────────────────────────

    async def _publish_video(
        self, ig_user_id: str, access_token: str, video_url: str, caption: str
    ) -> str:
        """Publish a video/reel post (container → poll status → publish)."""
        if not await is_safe_url_async(video_url):
            raise InstagramAPIError(
                code=InstagramErrorCode.INVALID_MEDIA, message="Unsafe media URL", retryable=False
            )

        # Step 1: Create video container
        container_id = await self._create_video_container(
            ig_user_id, access_token, video_url, caption
        )

        # Step 2: Wait for processing
        await self._wait_for_container(access_token, container_id)

        # Step 3: Publish
        return await self._publish_container(ig_user_id, access_token, container_id)

    # ── Carousel publishing ───────────────────────────────────────────────────

    async def _publish_carousel(
        self, ig_user_id: str, access_token: str, media_urls: list[str], caption: str
    ) -> str:
        """Publish a carousel post (create item containers → carousel container → publish)."""
        # Step 1: Create individual item containers
        child_ids: list[str] = []
        for url in media_urls[:10]:  # Max 10 items
            if not await is_safe_url_async(url):
                continue
            media_type = detect_media_type([url])
            if media_type == "video":
                cid = await self._create_video_container(
                    ig_user_id, access_token, url, "", is_carousel_item=True
                )
            else:
                cid = await self._create_image_container(
                    ig_user_id, access_token, url, "", is_carousel_item=True
                )
            child_ids.append(cid)

        if not child_ids:
            raise InstagramAPIError(
                code=InstagramErrorCode.INVALID_MEDIA,
                message="No valid media for carousel", retryable=False
            )

        # Step 2: Create carousel container
        carousel_id = await self._create_carousel_container(
            ig_user_id, access_token, child_ids, caption
        )

        # Step 3: Publish
        return await self._publish_container(ig_user_id, access_token, carousel_id)

    # ── Container creation ────────────────────────────────────────────────────

    async def _create_image_container(
        self,
        ig_user_id: str,
        access_token: str,
        image_url: str,
        caption: str,
        is_carousel_item: bool = False,
    ) -> str:
        """Create an image media container."""
        url = f"{GRAPH_API_HOST}/{API_VERSION}/{ig_user_id}/media"
        params: dict = {
            "image_url": image_url,
            "access_token": access_token,
        }
        if caption and not is_carousel_item:
            params["caption"] = caption
        if is_carousel_item:
            params["is_carousel_item"] = "true"

        return await self._post_and_get_id(url, params)

    async def _create_video_container(
        self,
        ig_user_id: str,
        access_token: str,
        video_url: str,
        caption: str,
        is_carousel_item: bool = False,
    ) -> str:
        """Create a video/reels media container."""
        url = f"{GRAPH_API_HOST}/{API_VERSION}/{ig_user_id}/media"
        params: dict = {
            "video_url": video_url,
            "media_type": "REELS",
            "access_token": access_token,
        }
        if caption and not is_carousel_item:
            params["caption"] = caption
        if is_carousel_item:
            params["is_carousel_item"] = "true"
            params["media_type"] = "VIDEO"

        return await self._post_and_get_id(url, params)

    async def _create_carousel_container(
        self,
        ig_user_id: str,
        access_token: str,
        child_ids: list[str],
        caption: str,
    ) -> str:
        """Create a carousel container from child containers."""
        url = f"{GRAPH_API_HOST}/{API_VERSION}/{ig_user_id}/media"
        params: dict = {
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "access_token": access_token,
        }
        if caption:
            params["caption"] = caption

        return await self._post_and_get_id(url, params)

    # ── Container publishing ──────────────────────────────────────────────────

    async def _publish_container(
        self, ig_user_id: str, access_token: str, container_id: str
    ) -> str:
        """Publish a media container. Returns the published media ID."""
        url = f"{GRAPH_API_HOST}/{API_VERSION}/{ig_user_id}/media_publish"
        params = {
            "creation_id": container_id,
            "access_token": access_token,
        }

        return await self._post_and_get_id(url, params)

    # ── Container status polling ──────────────────────────────────────────────

    async def _wait_for_container(self, access_token: str, container_id: str) -> None:
        """Poll container status until FINISHED or timeout."""
        url = f"{GRAPH_API_HOST}/{API_VERSION}/{container_id}"
        params = {"fields": "status_code", "access_token": access_token}
        elapsed = 0

        while elapsed < CONTAINER_POLL_MAX_WAIT:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                response = await client.get(url, params=params)

            if response.status_code == 200:
                data = response.json()
                status = data.get("status_code", "")
                if status == "FINISHED":
                    return
                if status == "ERROR":
                    raise InstagramAPIError(
                        code=InstagramErrorCode.INVALID_MEDIA,
                        message="Media container processing failed",
                        retryable=False,
                    )
                if status == "EXPIRED":
                    raise InstagramAPIError(
                        code=InstagramErrorCode.INVALID_MEDIA,
                        message="Media container expired",
                        retryable=False,
                    )

            await asyncio.sleep(CONTAINER_POLL_INTERVAL)
            elapsed += CONTAINER_POLL_INTERVAL

        raise InstagramAPIError(
            code=InstagramErrorCode.PLATFORM_UNAVAILABLE,
            message="Container processing timed out",
            retryable=True,
        )

    # ── HTTP helper ───────────────────────────────────────────────────────────

    async def _post_and_get_id(self, url: str, params: dict) -> str:
        """POST to a Graph API endpoint and return the 'id' from the response."""
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(url, data=params)

        if response.status_code == 200:
            data = response.json()
            container_id = data.get("id", "")
            if container_id:
                return container_id
            raise InstagramAPIError(
                code=InstagramErrorCode.UNKNOWN_PLATFORM_ERROR,
                message="API returned success but no ID",
                retryable=True,
            )

        # Error
        try:
            error_body = response.json()
        except Exception:
            error_body = {"error": {"message": response.text[:300]}}

        raise classify_meta_error(response.status_code, error_body)
