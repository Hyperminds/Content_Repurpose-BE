"""Mocked integration tests for the Facebook publishing integration.

Tests cover:
  - Page text post (success)
  - Page photo post (success)
  - Page link post (success)
  - Personal profile gets no publishing capability
  - No Pages available → account not eligible
  - Expired token (OAuthException)
  - Permission denied
  - Rate limiting (429)
  - API failures (500/503)
  - Invalid content (too long)
  - Invalid media format
  - Auth provider Page detection
  - Error classification
  - Media validation
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
from app.social_publishing.integrations.facebook.publisher import FacebookPublisher
from app.social_publishing.integrations.facebook.auth_provider import FacebookAuthProvider
from app.social_publishing.integrations.facebook.errors import (
    FacebookErrorCode,
    classify_facebook_error,
)
from app.social_publishing.integrations.facebook.media_validation import (
    detect_post_type,
    validate_for_publishing,
)
from app.social_publishing.integrations.facebook.constants import MAX_MESSAGE_LENGTH
from app.social_publishing.auth.provider import AccountInfo


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_post(
    content: str = "Hello Facebook!",
    media_urls: list[str] | None = None,
    account_id: str = "page_123456",
) -> SocialPost:
    return SocialPost(
        id="post_1",
        tenant_id="tenant_1",
        account_id=account_id,
        platform=SocialPlatform.META,
        status=PostStatus.QUEUED,
        content=content,
        media_urls=media_urls or [],
    )


def _mock_response(status_code: int, json_data: dict = None, headers: dict = None):
    resp = MagicMock(spec=Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}
    resp.text = str(json_data or "")
    return resp


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — TEXT POST
# ══════════════════════════════════════════════════════════════════════════════

class TestFacebookPublisherTextPost:
    @pytest.mark.asyncio
    async def test_text_post_success(self):
        publisher = FacebookPublisher()
        post = _make_post(content="Hello from Trendzzo!")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(
                200, json_data={"id": "page_123456_987654321"}
            )

            result = await publisher.publish(post, "page_token_abc")

        assert result.success is True
        assert result.platform_post_id == "page_123456_987654321"

    @pytest.mark.asyncio
    async def test_empty_content_no_media_fails(self):
        publisher = FacebookPublisher()
        post = _make_post(content="", media_urls=[])

        result = await publisher.publish(post, "page_token")

        assert result.success is False
        assert "Validation failed" in result.error_message
        assert result.retryable is False


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — PHOTO POST
# ══════════════════════════════════════════════════════════════════════════════

class TestFacebookPublisherPhotoPost:
    @pytest.mark.asyncio
    async def test_photo_post_success(self):
        publisher = FacebookPublisher()
        post = _make_post(
            content="Check this out!",
            media_urls=["https://cdn.example.com/photo.jpg"],
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(
                200, json_data={"id": "photo_111", "post_id": "page_123_post_222"}
            )

            result = await publisher.publish(post, "page_token")

        assert result.success is True
        assert "page_123_post_222" in result.platform_post_id


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — LINK POST
# ══════════════════════════════════════════════════════════════════════════════

class TestFacebookPublisherLinkPost:
    @pytest.mark.asyncio
    async def test_link_post_success(self):
        publisher = FacebookPublisher()
        post = _make_post(
            content="Great article!",
            media_urls=["https://blog.example.com/article"],
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(
                200, json_data={"id": "page_123_post_333"}
            )

            result = await publisher.publish(post, "page_token")

        assert result.success is True


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — ERROR SCENARIOS
# ══════════════════════════════════════════════════════════════════════════════

class TestFacebookPublisherErrors:
    @pytest.mark.asyncio
    async def test_expired_token_oauth_exception(self):
        publisher = FacebookPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(401, json_data={
                "error": {
                    "message": "Error validating access token",
                    "type": "OAuthException",
                    "code": 190,
                    "error_subcode": 463,
                }
            })

            result = await publisher.publish(post, "expired_token")

        assert result.success is False
        assert "authentication" in result.error_message.lower()
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        publisher = FacebookPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(403, json_data={
                "error": {"message": "Insufficient permission", "type": "OAuthException", "code": 10}
            })

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_rate_limited_429(self):
        publisher = FacebookPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(429, json_data={
                "error": {"message": "Too many calls", "code": 4}
            })

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_server_error_500(self):
        publisher = FacebookPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(500, json_data={
                "error": {"message": "Internal error"}
            })

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_content_too_long(self):
        publisher = FacebookPublisher()
        long_content = "x" * (MAX_MESSAGE_LENGTH + 1)
        post = _make_post(content=long_content)

        result = await publisher.publish(post, "token")

        assert result.success is False
        assert "character limit" in result.error_message.lower()
        assert result.retryable is False


# ══════════════════════════════════════════════════════════════════════════════
# AUTH PROVIDER — CAPABILITY DETECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestFacebookAuthProviderCapabilities:
    @pytest.mark.asyncio
    async def test_page_account_gets_publishing(self):
        provider = FacebookAuthProvider()
        account_info = AccountInfo(
            platform_account_id="page_123",
            account_name="My Page",
            account_type="page",
            email="",
        )

        capabilities = await provider.detect_capabilities("token", account_info)

        assert AccountCapability.TEXT_PUBLISHING in capabilities
        assert AccountCapability.IMAGE_PUBLISHING in capabilities
        assert AccountCapability.VIDEO_PUBLISHING in capabilities
        assert AccountCapability.SCHEDULED_PUBLISHING in capabilities

    @pytest.mark.asyncio
    async def test_personal_account_gets_no_publishing(self):
        provider = FacebookAuthProvider()
        account_info = AccountInfo(
            platform_account_id="user_456",
            account_name="John Doe",
            account_type="personal",
            email="john@example.com",
        )

        capabilities = await provider.detect_capabilities("token", account_info)

        assert capabilities == []
        assert AccountCapability.TEXT_PUBLISHING not in capabilities

    @pytest.mark.asyncio
    async def test_page_detection_via_category(self):
        provider = FacebookAuthProvider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = _mock_response(200, json_data={
                "id": "page_789",
                "name": "Business Page",
                "category": "Internet Company",
                "link": "https://facebook.com/businesspage",
            })

            info = await provider.get_account_info("page_token")

        assert info.account_type == "page"
        assert info.platform_account_id == "page_789"

    @pytest.mark.asyncio
    async def test_user_info_fallback_personal(self):
        provider = FacebookAuthProvider()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            # First call (page detection) returns no category
            # Second call (user info) returns user
            mock_client.get.side_effect = [
                _mock_response(200, json_data={"id": "user_111", "name": "Jane"}),
                _mock_response(200, json_data={"id": "user_111", "name": "Jane", "email": "jane@x.com"}),
            ]

            info = await provider.get_account_info("user_token")

        assert info.account_type == "personal"

    @pytest.mark.asyncio
    async def test_no_pages_returns_token_without_crash(self):
        provider = FacebookAuthProvider()
        provider._app_id = "test_app_id"
        provider._app_secret = "test_app_secret"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            # Token exchange success
            mock_client.get.side_effect = [
                # Short-lived token exchange
                _mock_response(200, json_data={"access_token": "short_token"}),
                # Long-lived token exchange
                _mock_response(200, json_data={"access_token": "long_token"}),
                # /me/accounts — empty pages
                _mock_response(200, json_data={"data": []}),
            ]

            result = await provider.exchange_code("code_abc", "http://redirect")

        # Should still return a token (user token) even without Pages
        assert result.access_token == "long_token"


# ══════════════════════════════════════════════════════════════════════════════
# ERROR CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class TestFacebookErrorClassification:
    def test_oauth_exception_190_auth_required(self):
        error = classify_facebook_error(401, {
            "error": {"type": "OAuthException", "code": 190, "message": "Invalid token"}
        })
        assert error.code == FacebookErrorCode.AUTHENTICATION_REQUIRED
        assert error.retryable is False

    def test_oauth_exception_10_permission(self):
        error = classify_facebook_error(403, {
            "error": {"type": "OAuthException", "code": 10, "message": "No permission"}
        })
        assert error.code == FacebookErrorCode.PERMISSION_DENIED

    def test_rate_limit_code_4(self):
        error = classify_facebook_error(429, {
            "error": {"code": 4, "message": "API calls limit"}
        })
        assert error.code == FacebookErrorCode.RATE_LIMITED
        assert error.retryable is True

    def test_rate_limit_code_17(self):
        error = classify_facebook_error(200, {
            "error": {"code": 17, "message": "User request limit reached"}
        })
        assert error.code == FacebookErrorCode.RATE_LIMITED

    def test_400_photo_invalid_media(self):
        error = classify_facebook_error(400, {
            "error": {"message": "Invalid photo format provided"}
        })
        assert error.code == FacebookErrorCode.INVALID_MEDIA

    def test_400_generic_invalid_content(self):
        error = classify_facebook_error(400, {
            "error": {"message": "Message is too long"}
        })
        assert error.code == FacebookErrorCode.INVALID_CONTENT

    def test_500_platform_unavailable(self):
        error = classify_facebook_error(500, {
            "error": {"message": "Internal error"}
        })
        assert error.code == FacebookErrorCode.PLATFORM_UNAVAILABLE
        assert error.retryable is True

    def test_sanitizes_token_in_message(self):
        error = classify_facebook_error(400, {
            "error": {"message": "Invalid access_token Bearer xyz123"}
        })
        assert "access_token" not in error.message
        assert "redacted" in error.message


# ══════════════════════════════════════════════════════════════════════════════
# MEDIA VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

class TestFacebookMediaValidation:
    def test_text_only_valid(self):
        errors = validate_for_publishing("Hello world", [])
        assert errors == []

    def test_empty_everything_invalid(self):
        errors = validate_for_publishing("", [])
        assert len(errors) > 0

    def test_message_too_long(self):
        long = "x" * (MAX_MESSAGE_LENGTH + 1)
        errors = validate_for_publishing(long, [])
        assert any("character limit" in e.lower() for e in errors)

    def test_valid_photo_url(self):
        errors = validate_for_publishing("Caption", ["https://cdn.example.com/photo.jpg"])
        assert errors == []

    def test_invalid_format_svg(self):
        errors = validate_for_publishing("Pic", ["https://cdn.example.com/logo.svg"])
        assert any("unsupported" in e.lower() for e in errors)

    def test_non_http_url_invalid(self):
        errors = validate_for_publishing("Test", ["ftp://server.com/file.jpg"])
        assert any("http" in e.lower() for e in errors)

    def test_detect_post_type_text(self):
        assert detect_post_type("Hello", [], "") == "text"

    def test_detect_post_type_photo(self):
        assert detect_post_type("Caption", ["https://x.com/img.jpg"], "") == "photo"

    def test_detect_post_type_video(self):
        assert detect_post_type("Video", ["https://x.com/vid.mp4"], "") == "video"

    def test_detect_post_type_link(self):
        assert detect_post_type("Article", [], "https://blog.example.com") == "link"
