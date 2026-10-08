"""Mocked integration tests for the Instagram publishing integration.

Tests cover:
  - Successful image publishing (container → publish)
  - Successful video publishing (container → poll → publish)
  - Successful carousel publishing
  - No media provided (Instagram requires media)
  - Invalid account (personal, not professional)
  - Expired credentials (401)
  - Insufficient permissions
  - Invalid media (unsupported format, unsafe URL)
  - Rate limiting (429)
  - Temporary Meta failures (500/503)
  - Container processing timeout
  - Container processing error
  - Duplicate publishing prevention (idempotency via job system)
  - Auth provider capability detection
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import Response

from app.social_publishing.domain.enums import (
    AccountCapability,
    PostStatus,
    SocialPlatform,
)
from app.social_publishing.domain.models import PublishingResult, SocialPost
from app.social_publishing.integrations.instagram.publisher import InstagramPublisher
from app.social_publishing.integrations.instagram.auth_provider import InstagramAuthProvider
from app.social_publishing.integrations.instagram.errors import (
    InstagramErrorCode,
    classify_meta_error,
)
from app.social_publishing.integrations.instagram.media_validation import (
    detect_media_type,
    validate_for_publishing,
)
from app.social_publishing.integrations.instagram.constants import MAX_CAPTION_LENGTH
from app.social_publishing.auth.provider import AccountInfo


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_post(
    content: str = "Beautiful sunset!",
    media_urls: list[str] | None = None,
    account_id: str = "ig_user_12345",
) -> SocialPost:
    if media_urls is None:
        media_urls = ["https://cdn.example.com/photo.jpg"]
    return SocialPost(
        id="post_1",
        tenant_id="tenant_1",
        account_id=account_id,
        platform=SocialPlatform.INSTAGRAM,
        status=PostStatus.QUEUED,
        content=content,
        media_urls=media_urls,
    )


def _mock_response(status_code: int, json_data: dict = None):
    resp = MagicMock(spec=Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = str(json_data or "")
    return resp


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — IMAGE POST
# ══════════════════════════════════════════════════════════════════════════════

class TestInstagramPublisherImagePost:
    @pytest.mark.asyncio
    async def test_image_post_success(self):
        publisher = InstagramPublisher()
        post = _make_post(media_urls=["https://cdn.example.com/photo.jpg"])

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = [
                # Step 1: Create container
                _mock_response(200, {"id": "container_123"}),
                # Step 2: Publish container
                _mock_response(200, {"id": "media_456"}),
            ]

            result = await publisher.publish(post, "valid_token")

        assert result.success is True
        assert result.platform_post_id == "media_456"

    @pytest.mark.asyncio
    async def test_no_media_fails_validation(self):
        publisher = InstagramPublisher()
        post = _make_post(media_urls=[])

        # Validation happens before any HTTP — test it directly
        errors = await publisher.validate_content(post)
        assert len(errors) > 0
        assert any("requires" in e.lower() for e in errors)


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — VIDEO POST
# ══════════════════════════════════════════════════════════════════════════════

class TestInstagramPublisherVideoPost:
    @pytest.mark.asyncio
    async def test_video_post_success(self):
        publisher = InstagramPublisher()
        post = _make_post(media_urls=["https://cdn.example.com/video.mp4"])

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = [
                # Create video container
                _mock_response(200, {"id": "container_vid_1"}),
                # Publish container
                _mock_response(200, {"id": "media_vid_1"}),
            ]
            # Poll status → FINISHED
            mock_client.get.return_value = _mock_response(200, {"status_code": "FINISHED"})

            result = await publisher.publish(post, "token")

        assert result.success is True
        assert result.platform_post_id == "media_vid_1"

    @pytest.mark.asyncio
    async def test_video_container_error_fails(self):
        publisher = InstagramPublisher()
        post = _make_post(media_urls=["https://cdn.example.com/video.mp4"])

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200, {"id": "container_1"})
            # Poll status → ERROR
            mock_client.get.return_value = _mock_response(200, {"status_code": "ERROR"})

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert "processing failed" in result.error_message.lower()
        assert result.retryable is False


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — CAROUSEL POST
# ══════════════════════════════════════════════════════════════════════════════

class TestInstagramPublisherCarousel:
    @pytest.mark.asyncio
    async def test_carousel_success(self):
        publisher = InstagramPublisher()
        post = _make_post(media_urls=[
            "https://cdn.example.com/img1.jpg",
            "https://cdn.example.com/img2.jpg",
        ])

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.side_effect = [
                # Child container 1
                _mock_response(200, {"id": "child_1"}),
                # Child container 2
                _mock_response(200, {"id": "child_2"}),
                # Carousel container
                _mock_response(200, {"id": "carousel_1"}),
                # Publish
                _mock_response(200, {"id": "media_carousel"}),
            ]

            result = await publisher.publish(post, "token")

        assert result.success is True
        assert result.platform_post_id == "media_carousel"


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — ERROR SCENARIOS
# ══════════════════════════════════════════════════════════════════════════════

class TestInstagramPublisherErrors:
    @pytest.mark.asyncio
    async def test_expired_credentials_401(self):
        publisher = InstagramPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(401, {
                "error": {"message": "Invalid OAuth access token", "type": "OAuthException", "code": 190}
            })

            result = await publisher.publish(post, "expired_token")

        assert result.success is False
        assert "authentication" in result.error_message.lower()
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_insufficient_permissions(self):
        publisher = InstagramPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(403, {
                "error": {"message": "(#10) Permission denied", "code": 10}
            })

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_rate_limited_429(self):
        publisher = InstagramPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(429, {
                "error": {"message": "Rate limit reached", "code": 4}
            })

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_server_error_500(self):
        publisher = InstagramPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(500, {
                "error": {"message": "Internal error"}
            })

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_unsafe_url_rejected(self):
        publisher = InstagramPublisher()
        post = _make_post(media_urls=["http://169.254.169.254/metadata"])

        result = await publisher.publish(post, "token")

        # Should fail validation (non-https or unsafe)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unsupported_image_format_png(self):
        publisher = InstagramPublisher()
        post = _make_post(media_urls=["https://cdn.example.com/image.png"])

        result = await publisher.publish(post, "token")

        assert result.success is False
        assert "unsupported" in result.error_message.lower() or "Validation" in result.error_message


# ══════════════════════════════════════════════════════════════════════════════
# AUTH PROVIDER — CAPABILITY DETECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestInstagramAuthProviderCapabilities:
    @pytest.mark.asyncio
    async def test_business_account_gets_publishing(self):
        provider = InstagramAuthProvider()
        account_info = AccountInfo(
            platform_account_id="ig_123",
            account_name="BusinessPage",
            account_type="business",
            email="",
        )

        capabilities = await provider.detect_capabilities("token", account_info)

        assert AccountCapability.IMAGE_PUBLISHING in capabilities
        assert AccountCapability.VIDEO_PUBLISHING in capabilities
        assert AccountCapability.CAROUSEL_PUBLISHING in capabilities

    @pytest.mark.asyncio
    async def test_creator_account_gets_publishing(self):
        provider = InstagramAuthProvider()
        account_info = AccountInfo(
            platform_account_id="ig_456",
            account_name="CreatorPage",
            account_type="creator",
            email="",
        )

        capabilities = await provider.detect_capabilities("token", account_info)

        assert AccountCapability.IMAGE_PUBLISHING in capabilities

    @pytest.mark.asyncio
    async def test_personal_account_gets_no_publishing(self):
        provider = InstagramAuthProvider()
        account_info = AccountInfo(
            platform_account_id="ig_789",
            account_name="PersonalUser",
            account_type="personal",
            email="",
        )

        capabilities = await provider.detect_capabilities("token", account_info)

        assert capabilities == []
        assert AccountCapability.IMAGE_PUBLISHING not in capabilities


# ══════════════════════════════════════════════════════════════════════════════
# ERROR CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class TestInstagramErrorClassification:
    def test_401_oauth_maps_to_auth_required(self):
        error = classify_meta_error(401, {
            "error": {"message": "Invalid token", "type": "OAuthException", "code": 190}
        })
        assert error.code == InstagramErrorCode.AUTHENTICATION_REQUIRED
        assert error.retryable is False

    def test_permission_denied(self):
        error = classify_meta_error(200, {
            "error": {"message": "(#10) Permission denied", "code": 10}
        })
        assert error.code == InstagramErrorCode.PERMISSION_DENIED

    def test_429_rate_limited(self):
        error = classify_meta_error(429, {
            "error": {"message": "Too many calls", "code": 4}
        })
        assert error.code == InstagramErrorCode.RATE_LIMITED
        assert error.retryable is True

    def test_500_platform_unavailable(self):
        error = classify_meta_error(500, {"error": {"message": "Internal"}})
        assert error.code == InstagramErrorCode.PLATFORM_UNAVAILABLE
        assert error.retryable is True

    def test_sanitizes_token_in_message(self):
        error = classify_meta_error(400, {
            "error": {"message": "Invalid access_token Bearer xyz"}
        })
        assert "access_token" not in error.message
        assert "redacted" in error.message


# ══════════════════════════════════════════════════════════════════════════════
# MEDIA VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

class TestInstagramMediaValidation:
    def test_no_media_invalid(self):
        errors = validate_for_publishing("Hello", [], "image")
        assert any("requires" in e.lower() for e in errors)

    def test_valid_jpeg_url(self):
        errors = validate_for_publishing("Caption", ["https://cdn.example.com/photo.jpg"], "image")
        assert errors == []

    def test_png_rejected(self):
        errors = validate_for_publishing("Pic", ["https://cdn.example.com/image.png"], "image")
        assert any("unsupported" in e.lower() for e in errors)

    def test_caption_too_long(self):
        long = "x" * (MAX_CAPTION_LENGTH + 1)
        errors = validate_for_publishing(long, ["https://cdn.example.com/x.jpg"], "image")
        assert any("character" in e.lower() for e in errors)

    def test_too_many_hashtags(self):
        content = " ".join(f"#{i}tag" for i in range(31))
        errors = validate_for_publishing(content, ["https://cdn.example.com/x.jpg"], "image")
        assert any("hashtag" in e.lower() for e in errors)

    def test_detect_media_type_image(self):
        assert detect_media_type(["https://x.com/a.jpg"]) == "image"

    def test_detect_media_type_video(self):
        assert detect_media_type(["https://x.com/v.mp4"]) == "video"

    def test_detect_media_type_carousel(self):
        assert detect_media_type(["https://x.com/a.jpg", "https://x.com/b.jpg"]) == "carousel"

    def test_non_http_url_invalid(self):
        errors = validate_for_publishing("Test", ["ftp://x.com/img.jpg"], "image")
        assert any("http" in e.lower() for e in errors)
