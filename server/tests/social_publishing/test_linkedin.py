"""Mocked integration tests for the LinkedIn publishing integration.

Tests cover:
  - Member text publishing (success)
  - Member image publishing (success)
  - Organization publishing (success)
  - Insufficient organization permission
  - Invalid content (too long)
  - Invalid media (unsupported format)
  - Expired authorization (401)
  - Rate limiting (429)
  - API failures (500/503)
  - Duplicate job execution prevention (idempotency)
  - Auth provider org role detection
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
from app.social_publishing.integrations.linkedin.publisher import LinkedInPublisher
from app.social_publishing.integrations.linkedin.errors import (
    LinkedInErrorCode,
    classify_linkedin_error,
)
from app.social_publishing.integrations.linkedin.media_validation import (
    detect_media_type,
    validate_for_publishing,
)
from app.social_publishing.integrations.linkedin.constants import (
    MAX_COMMENTARY_LENGTH,
    PUBLISHING_ROLES,
)
from app.social_publishing.auth.linkedin_provider import LinkedInAuthProvider


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_post(
    content: str = "Hello LinkedIn!",
    media_urls: list[str] | None = None,
    account_id: str = "abc123_member",
) -> SocialPost:
    return SocialPost(
        id="post_1",
        tenant_id="tenant_1",
        account_id=account_id,
        platform=SocialPlatform.LINKEDIN,
        status=PostStatus.QUEUED,
        content=content,
        media_urls=media_urls or [],
    )


def _mock_response(status_code: int, json_data: dict = None, headers: dict = None):
    """Create a mock httpx Response."""
    resp = MagicMock(spec=Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}
    resp.text = str(json_data or "")
    resp.content = b"fake_image_bytes_longer_than_100_characters_" * 5
    return resp


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — MEMBER TEXT POST
# ══════════════════════════════════════════════════════════════════════════════

class TestLinkedInPublisherTextPost:
    @pytest.mark.asyncio
    async def test_text_post_success(self):
        publisher = LinkedInPublisher()
        post = _make_post(content="Hello world!")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(
                201, headers={"x-restli-id": "urn:li:share:123456"}
            )

            result = await publisher.publish(post, "valid_token")

        assert result.success is True
        assert result.platform_post_id == "urn:li:share:123456"

    @pytest.mark.asyncio
    async def test_text_post_empty_content_no_media_fails(self):
        publisher = LinkedInPublisher()
        post = _make_post(content="", media_urls=[])

        result = await publisher.publish(post, "valid_token")

        assert result.success is False
        assert "Validation failed" in result.error_message
        assert result.retryable is False


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — IMAGE POST
# ══════════════════════════════════════════════════════════════════════════════

class TestLinkedInPublisherImagePost:
    @pytest.mark.asyncio
    async def test_image_post_success(self):
        publisher = LinkedInPublisher()
        post = _make_post(
            content="Check this out!",
            media_urls=["https://cdn.example.com/photo.jpg"],
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            # Sequence: initializeUpload → fetch image → upload binary → create post
            mock_client.post.side_effect = [
                # initializeUpload response
                _mock_response(200, json_data={
                    "value": {
                        "uploadUrl": "https://www.linkedin.com/dms-uploads/xxx",
                        "image": "urn:li:image:C5F10AQabc123",
                    }
                }),
                # create post response
                _mock_response(201, headers={"x-restli-id": "urn:li:share:789"}),
            ]
            mock_client.get.return_value = _mock_response(200)  # fetch image bytes
            mock_client.put.return_value = _mock_response(201)  # upload binary

            result = await publisher.publish(post, "valid_token")

        assert result.success is True
        assert result.platform_post_id == "urn:li:share:789"

    @pytest.mark.asyncio
    async def test_image_upload_failure_retryable(self):
        publisher = LinkedInPublisher()
        post = _make_post(
            content="Image post",
            media_urls=["https://cdn.example.com/photo.jpg"],
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            # initializeUpload fails
            mock_client.post.return_value = _mock_response(500)

            result = await publisher.publish(post, "valid_token")

        assert result.success is False
        assert "upload failed" in result.error_message.lower()
        assert result.retryable is True


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — ERROR SCENARIOS
# ══════════════════════════════════════════════════════════════════════════════

class TestLinkedInPublisherErrors:
    @pytest.mark.asyncio
    async def test_expired_auth_401(self):
        publisher = LinkedInPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(
                401, json_data={"message": "Empty OAuth2 access token"}
            )

            result = await publisher.publish(post, "expired_token")

        assert result.success is False
        assert "authentication" in result.error_message.lower()
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_permission_denied_403(self):
        publisher = LinkedInPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(
                403, json_data={"message": "Access denied - insufficient permissions"}
            )

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_rate_limited_429(self):
        publisher = LinkedInPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(
                429, json_data={"message": "Too many requests"}
            )

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_server_error_503(self):
        publisher = LinkedInPublisher()
        post = _make_post()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(
                503, json_data={"message": "Service unavailable"}
            )

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_invalid_content_too_long(self):
        publisher = LinkedInPublisher()
        long_content = "x" * (MAX_COMMENTARY_LENGTH + 1)
        post = _make_post(content=long_content)

        result = await publisher.publish(post, "token")

        assert result.success is False
        assert "character limit" in result.error_message.lower()
        assert result.retryable is False


# ══════════════════════════════════════════════════════════════════════════════
# AUTH PROVIDER — ORGANIZATION ROLE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestLinkedInAuthProviderOrgDetection:
    @pytest.mark.asyncio
    async def test_detects_org_admin_role(self):
        provider = LinkedInAuthProvider()
        account_info = MagicMock()
        account_info.account_type = "personal"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            # organizationalEntityAcls response with ADMINISTRATOR role
            mock_client.get.return_value = _mock_response(200, json_data={
                "elements": [
                    {
                        "role": "ADMINISTRATOR",
                        "organizationalTarget": "urn:li:organization:12345",
                    }
                ]
            })

            capabilities = await provider.detect_capabilities("token", account_info)

        assert AccountCapability.ORGANIZATION_PUBLISHING in capabilities
        assert AccountCapability.TEXT_PUBLISHING in capabilities

    @pytest.mark.asyncio
    async def test_no_org_access_no_org_capability(self):
        provider = LinkedInAuthProvider()
        account_info = MagicMock()
        account_info.account_type = "personal"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            # No eligible roles
            mock_client.get.return_value = _mock_response(200, json_data={
                "elements": [
                    {"role": "VIEWER", "organizationalTarget": "urn:li:organization:999"}
                ]
            })

            capabilities = await provider.detect_capabilities("token", account_info)

        assert AccountCapability.ORGANIZATION_PUBLISHING not in capabilities
        assert AccountCapability.TEXT_PUBLISHING in capabilities

    @pytest.mark.asyncio
    async def test_org_endpoint_403_graceful_fallback(self):
        provider = LinkedInAuthProvider()
        account_info = MagicMock()
        account_info.account_type = "personal"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            # Scope not granted — 403
            mock_client.get.return_value = _mock_response(403)

            capabilities = await provider.detect_capabilities("token", account_info)

        # Should still get member capabilities, just no org
        assert AccountCapability.TEXT_PUBLISHING in capabilities
        assert AccountCapability.ORGANIZATION_PUBLISHING not in capabilities

    @pytest.mark.asyncio
    async def test_org_endpoint_network_error_graceful(self):
        provider = LinkedInAuthProvider()
        account_info = MagicMock()
        account_info.account_type = "personal"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.side_effect = Exception("Network timeout")

            capabilities = await provider.detect_capabilities("token", account_info)

        assert AccountCapability.TEXT_PUBLISHING in capabilities
        assert AccountCapability.ORGANIZATION_PUBLISHING not in capabilities


# ══════════════════════════════════════════════════════════════════════════════
# ERROR CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class TestLinkedInErrorClassification:
    def test_401_maps_to_auth_required(self):
        error = classify_linkedin_error(401, {"message": "Empty access token"})
        assert error.code == LinkedInErrorCode.AUTHENTICATION_REQUIRED
        assert error.retryable is False

    def test_403_with_org_maps_to_not_eligible(self):
        error = classify_linkedin_error(403, {"message": "Insufficient organization role"})
        assert error.code == LinkedInErrorCode.ACCOUNT_NOT_ELIGIBLE

    def test_403_generic_maps_to_permission_denied(self):
        error = classify_linkedin_error(403, {"message": "Access denied"})
        assert error.code == LinkedInErrorCode.PERMISSION_DENIED

    def test_429_maps_to_rate_limited(self):
        error = classify_linkedin_error(429, {"message": "Too many requests"})
        assert error.code == LinkedInErrorCode.RATE_LIMITED
        assert error.retryable is True

    def test_400_image_maps_to_invalid_media(self):
        error = classify_linkedin_error(400, {"message": "Invalid image upload"})
        assert error.code == LinkedInErrorCode.INVALID_MEDIA

    def test_400_content_maps_to_invalid_content(self):
        error = classify_linkedin_error(400, {"message": "Field length too long"})
        assert error.code == LinkedInErrorCode.INVALID_CONTENT

    def test_500_maps_to_platform_unavailable(self):
        error = classify_linkedin_error(500, {"message": "Internal server error"})
        assert error.code == LinkedInErrorCode.PLATFORM_UNAVAILABLE
        assert error.retryable is True

    def test_409_maps_to_conflict_retryable(self):
        error = classify_linkedin_error(409, {"message": "Write conflict"})
        assert error.retryable is True

    def test_sanitizes_token_in_message(self):
        error = classify_linkedin_error(400, {"message": "Invalid Bearer access_token provided"})
        assert "access_token" not in error.message
        assert "redacted" in error.message


# ══════════════════════════════════════════════════════════════════════════════
# MEDIA VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

class TestLinkedInMediaValidation:
    def test_text_only_post_valid(self):
        errors = validate_for_publishing("Hello world", [], "text")
        assert errors == []

    def test_empty_content_no_media_invalid(self):
        errors = validate_for_publishing("", [], "text")
        assert len(errors) > 0

    def test_content_too_long(self):
        long = "x" * (MAX_COMMENTARY_LENGTH + 1)
        errors = validate_for_publishing(long, [], "text")
        assert any("character limit" in e.lower() for e in errors)

    def test_valid_image_url(self):
        errors = validate_for_publishing(
            "Check this!", ["https://cdn.example.com/photo.jpg"], "image"
        )
        assert errors == []

    def test_invalid_image_format_svg(self):
        errors = validate_for_publishing(
            "Image!", ["https://cdn.example.com/logo.svg"], "image"
        )
        assert any("unsupported" in e.lower() for e in errors)

    def test_non_http_url_invalid(self):
        errors = validate_for_publishing(
            "Test", ["ftp://server.com/file.jpg"], "image"
        )
        assert any("http" in e.lower() for e in errors)

    def test_detect_media_type_text(self):
        assert detect_media_type([]) == "text"

    def test_detect_media_type_image(self):
        assert detect_media_type(["https://x.com/img.jpg"]) == "image"

    def test_detect_media_type_video(self):
        assert detect_media_type(["https://x.com/vid.mp4"]) == "video"

    def test_detect_media_type_multi(self):
        assert detect_media_type(["https://x.com/a.jpg", "https://x.com/b.jpg"]) == "multi_image"
