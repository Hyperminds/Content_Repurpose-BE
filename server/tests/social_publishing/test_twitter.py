"""Mocked integration tests for the X (Twitter) publishing integration.

Tests cover:
  - PKCE challenge generation (S256, verifier/challenge relationship)
  - Authorization URL construction (scopes, challenge, state)
  - Token exchange (code + verifier → tokens)
  - Token refresh (rotation of refresh_token)
  - Account info fetch (GET /2/users/me)
  - Capability detection
  - Text post publishing
  - Image post publishing (media upload → tweet with media_ids)
  - Text-only allowed / empty post rejected
  - Character-limit validation
  - Too many media items
  - Unsafe media URL (SSRF) rejected
  - Error classification (401, 403 duplicate, 403 access tier, 429, 400, 5xx)

No real network calls are made — httpx is mocked throughout.
"""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import Response

from app.social_publishing.domain.enums import (
    AccountCapability,
    PostStatus,
    SocialPlatform,
)
from app.social_publishing.domain.models import SocialPost
from app.social_publishing.auth.provider import AccountInfo, PKCEChallenge
from app.social_publishing.integrations.twitter.auth_provider import TwitterAuthProvider
from app.social_publishing.integrations.twitter.publisher import TwitterPublisher
from app.social_publishing.integrations.twitter.errors import (
    TwitterErrorCode,
    classify_twitter_error,
)
from app.social_publishing.integrations.twitter.media_validation import (
    detect_media_type,
    validate_for_publishing,
)
from app.social_publishing.integrations.twitter.constants import (
    MAX_MEDIA_ITEMS,
    MAX_TWEET_LENGTH,
    REQUIRED_SCOPES,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_post(
    content: str = "Hello from Trendzzo!",
    media_urls: list[str] | None = None,
    account_id: str = "x_user_123",
) -> SocialPost:
    return SocialPost(
        id="post_1",
        tenant_id="tenant_1",
        account_id=account_id,
        platform=SocialPlatform.TWITTER,
        status=PostStatus.QUEUED,
        content=content,
        media_urls=media_urls or [],
    )


def _mock_response(
    status_code: int,
    json_data: dict | None = None,
    url: str = "",
    content_type: str = "image/jpeg",
):
    resp = MagicMock(spec=Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = str(json_data or "")
    resp.content = b"fake-bytes"
    # httpx.Response.url reflects the FINAL url after redirects
    resp.url = url
    resp.headers = {"content-type": content_type}
    return resp


def _provider_with_creds(secret: str = "") -> TwitterAuthProvider:
    provider = TwitterAuthProvider()
    provider._client_id = "test_client_id"
    provider._client_secret = secret
    return provider


# ══════════════════════════════════════════════════════════════════════════════
# AUTH PROVIDER — PKCE
# ══════════════════════════════════════════════════════════════════════════════

class TestTwitterPKCE:
    def test_create_pkce_challenge_is_s256(self):
        provider = TwitterAuthProvider()
        pkce = provider.create_pkce_challenge()

        assert isinstance(pkce, PKCEChallenge)
        assert pkce.method == "S256"
        # challenge must be the base64url(sha256(verifier)) without padding
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(pkce.verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert pkce.challenge == expected
        assert "=" not in pkce.challenge

    def test_pkce_challenges_are_unique(self):
        provider = TwitterAuthProvider()
        a = provider.create_pkce_challenge()
        b = provider.create_pkce_challenge()
        assert a.verifier != b.verifier
        assert a.challenge != b.challenge


# ══════════════════════════════════════════════════════════════════════════════
# AUTH PROVIDER — AUTH URL
# ══════════════════════════════════════════════════════════════════════════════

class TestTwitterAuthUrl:
    def test_auth_url_contains_required_params(self):
        provider = _provider_with_creds()
        url = provider.get_auth_url(
            state="state_abc",
            redirect_uri="https://app.example.com/social-publishing/callback/twitter",
            code_challenge="challenge_xyz",
        )
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)

        assert qs["response_type"] == ["code"]
        assert qs["client_id"] == ["test_client_id"]
        assert qs["state"] == ["state_abc"]
        assert qs["code_challenge"] == ["challenge_xyz"]
        assert qs["code_challenge_method"] == ["S256"]
        assert qs["scope"][0] == " ".join(REQUIRED_SCOPES)

    def test_auth_url_requires_challenge(self):
        provider = _provider_with_creds()
        with pytest.raises(Exception):
            provider.get_auth_url(state="s", redirect_uri="https://x/cb", code_challenge="")

    def test_auth_url_requires_client_id(self):
        provider = TwitterAuthProvider()
        provider._client_id = ""
        with pytest.raises(Exception):
            provider.get_auth_url(state="s", redirect_uri="https://x/cb", code_challenge="c")


# ══════════════════════════════════════════════════════════════════════════════
# AUTH PROVIDER — TOKEN EXCHANGE / REFRESH
# ══════════════════════════════════════════════════════════════════════════════

class TestTwitterTokenExchange:
    @pytest.mark.asyncio
    async def test_exchange_code_success(self):
        provider = _provider_with_creds()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200, {
                "access_token": "at_1",
                "refresh_token": "rt_1",
                "expires_in": 7200,
                "token_type": "bearer",
            })

            result = await provider.exchange_code(
                code="auth_code", redirect_uri="https://x/cb", code_verifier="verifier_1"
            )

        assert result.access_token == "at_1"
        assert result.refresh_token == "rt_1"
        assert result.expires_in_seconds == 7200

    @pytest.mark.asyncio
    async def test_exchange_code_requires_verifier(self):
        provider = _provider_with_creds()
        with pytest.raises(Exception):
            await provider.exchange_code(
                code="c", redirect_uri="https://x/cb", code_verifier=""
            )

    @pytest.mark.asyncio
    async def test_refresh_token_rotates(self):
        provider = _provider_with_creds()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200, {
                "access_token": "at_2",
                "refresh_token": "rt_2",  # rotated
                "expires_in": 7200,
            })

            result = await provider.refresh_token("rt_1")

        assert result is not None
        assert result.access_token == "at_2"
        assert result.refresh_token == "rt_2"

    @pytest.mark.asyncio
    async def test_refresh_token_failure_returns_none(self):
        provider = _provider_with_creds()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(400, {"error": "invalid_grant"})

            result = await provider.refresh_token("rt_bad")

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_refresh_token_returns_none(self):
        provider = _provider_with_creds()
        assert await provider.refresh_token("") is None


# ══════════════════════════════════════════════════════════════════════════════
# AUTH PROVIDER — ACCOUNT INFO / CAPABILITIES
# ══════════════════════════════════════════════════════════════════════════════

class TestTwitterAccountInfo:
    @pytest.mark.asyncio
    async def test_get_account_info(self):
        provider = _provider_with_creds()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = _mock_response(200, {
                "data": {
                    "id": "999",
                    "username": "trendzzo",
                    "name": "Trendzzo Official",
                    "profile_image_url": "https://pbs.example/avatar.jpg",
                }
            })

            info = await provider.get_account_info("token")

        assert isinstance(info, AccountInfo)
        assert info.platform_account_id == "999"
        assert info.account_name == "Trendzzo Official"
        assert info.profile_url == "https://x.com/trendzzo"

    @pytest.mark.asyncio
    async def test_get_account_info_failure_raises(self):
        provider = _provider_with_creds()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = _mock_response(401, {})

            with pytest.raises(Exception):
                await provider.get_account_info("bad_token")

    @pytest.mark.asyncio
    async def test_detect_capabilities(self):
        provider = _provider_with_creds()
        info = AccountInfo(platform_account_id="1", account_name="X")
        caps = await provider.detect_capabilities("token", info)

        assert AccountCapability.TEXT_PUBLISHING in caps
        assert AccountCapability.IMAGE_PUBLISHING in caps
        assert AccountCapability.VIDEO_PUBLISHING in caps

    def test_platform_is_twitter(self):
        assert TwitterAuthProvider().platform == SocialPlatform.TWITTER


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — TEXT POST
# ══════════════════════════════════════════════════════════════════════════════

class TestTwitterPublisherText:
    @pytest.mark.asyncio
    async def test_text_post_success(self):
        publisher = TwitterPublisher()
        post = _make_post(content="Just a text post")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(201, {
                "data": {"id": "tweet_1", "text": "Just a text post"}
            })

            result = await publisher.publish(post, "token")

        assert result.success is True
        assert result.platform_post_id == "tweet_1"

    @pytest.mark.asyncio
    async def test_empty_post_fails_validation(self):
        publisher = TwitterPublisher()
        post = _make_post(content="", media_urls=[])
        errors = await publisher.validate_content(post)
        assert len(errors) > 0

    @pytest.mark.asyncio
    async def test_over_limit_fails_validation(self):
        publisher = TwitterPublisher()
        post = _make_post(content="x" * (MAX_TWEET_LENGTH + 1))
        errors = await publisher.validate_content(post)
        assert any("character" in e.lower() for e in errors)

    @pytest.mark.asyncio
    async def test_too_many_media_fails_validation(self):
        publisher = TwitterPublisher()
        urls = [f"https://cdn.example.com/img{i}.jpg" for i in range(MAX_MEDIA_ITEMS + 1)]
        post = _make_post(content="hi", media_urls=urls)
        errors = await publisher.validate_content(post)
        assert any("media" in e.lower() for e in errors)


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — IMAGE POST
# ══════════════════════════════════════════════════════════════════════════════

class TestTwitterPublisherImage:
    @pytest.mark.asyncio
    async def test_image_post_success(self):
        publisher = TwitterPublisher()
        post = _make_post(content="With image", media_urls=["https://cdn.example.com/a.jpg"])

        with patch(
            "app.social_publishing.integrations.twitter.publisher.is_safe_url_async",
            new=AsyncMock(return_value=True),
        ), patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            # v2 chunked flow: GET(fetch) then INIT, APPEND, FINALIZE, then tweet
            mock_client.get.return_value = _mock_response(200, {}, url="https://cdn.example.com/a.jpg")
            mock_client.post.side_effect = [
                _mock_response(200, {"data": {"id": "media_1"}}),   # INIT
                _mock_response(204, {}),                            # APPEND
                _mock_response(200, {"data": {"id": "media_1"}}),   # FINALIZE (no processing_info)
                _mock_response(201, {"data": {"id": "tweet_img_1"}}),  # tweet
            ]

            result = await publisher.publish(post, "token")

        assert result.success is True
        assert result.platform_post_id == "tweet_img_1"

    @pytest.mark.asyncio
    async def test_image_fetch_follows_redirect_to_safe_url(self):
        """A CDN 302 → safe final URL must succeed (the common Cloudinary case)."""
        publisher = TwitterPublisher()
        post = _make_post(content="pic", media_urls=["https://cdn.example.com/a.jpg"])

        with patch(
            "app.social_publishing.integrations.twitter.publisher.is_safe_url_async",
            new=AsyncMock(return_value=True),
        ), patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            # Final resolved URL differs from the requested one (redirect followed)
            mock_client.get.return_value = _mock_response(
                200, {}, url="https://res.cloudinary.com/final/a.jpg"
            )
            mock_client.post.side_effect = [
                _mock_response(200, {"data": {"id": "media_1"}}),   # INIT
                _mock_response(204, {}),                            # APPEND
                _mock_response(200, {"data": {"id": "media_1"}}),   # FINALIZE
                _mock_response(201, {"data": {"id": "tweet_2"}}),   # tweet
            ]

            result = await publisher.publish(post, "token")

        assert result.success is True
        assert result.platform_post_id == "tweet_2"

    @pytest.mark.asyncio
    async def test_image_waits_for_async_processing(self):
        """When FINALIZE returns processing_info, STATUS is polled to completion."""
        publisher = TwitterPublisher()
        post = _make_post(content="pic", media_urls=["https://cdn.example.com/a.jpg"])

        with patch(
            "app.social_publishing.integrations.twitter.publisher.is_safe_url_async",
            new=AsyncMock(return_value=True),
        ), patch(
            "app.social_publishing.integrations.twitter.publisher.asyncio.sleep",
            new=AsyncMock(return_value=None),
        ), patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            # GET is used for both the media fetch and the STATUS poll
            mock_client.get.side_effect = [
                _mock_response(200, {}, url="https://cdn.example.com/a.jpg"),          # fetch
                _mock_response(200, {"data": {"processing_info": {"state": "in_progress"}}}),  # STATUS 1
                _mock_response(200, {"data": {"processing_info": {"state": "succeeded"}}}),     # STATUS 2
            ]
            mock_client.post.side_effect = [
                _mock_response(200, {"data": {"id": "media_1"}}),  # INIT
                _mock_response(204, {}),                           # APPEND
                _mock_response(200, {"data": {"processing_info": {"state": "pending"}}}),  # FINALIZE (async)
                _mock_response(201, {"data": {"id": "tweet_vid"}}),  # tweet
            ]

            result = await publisher.publish(post, "token")

        assert result.success is True
        assert result.platform_post_id == "tweet_vid"

    @pytest.mark.asyncio
    async def test_image_redirect_to_unsafe_url_rejected(self):
        """A redirect landing on an unsafe host must be blocked (SSRF guard)."""
        publisher = TwitterPublisher()
        post = _make_post(content="pic", media_urls=["https://cdn.example.com/a.jpg"])

        # First check (initial URL) passes; second check (final URL) fails.
        safe_then_unsafe = AsyncMock(side_effect=[True, False])
        with patch(
            "app.social_publishing.integrations.twitter.publisher.is_safe_url_async",
            new=safe_then_unsafe,
        ), patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = _mock_response(
                200, {}, url="http://169.254.169.254/latest/meta-data"
            )

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert "unsafe" in result.error_message.lower()
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_unsafe_media_url_rejected(self):
        publisher = TwitterPublisher()
        post = _make_post(content="bad", media_urls=["http://169.254.169.254/latest"])

        with patch(
            "app.social_publishing.integrations.twitter.publisher.is_safe_url_async",
            new=AsyncMock(return_value=False),
        ):
            result = await publisher.publish(post, "token")

        assert result.success is False
        assert "unsafe" in result.error_message.lower()
        assert result.retryable is False

    def test_platform_name(self):
        assert TwitterPublisher().platform_name == "twitter"


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHER — API ERROR HANDLING
# ══════════════════════════════════════════════════════════════════════════════

class TestTwitterPublisherErrors:
    @pytest.mark.asyncio
    async def test_401_marks_auth_required_not_retryable(self):
        publisher = TwitterPublisher()
        post = _make_post(content="hi")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(401, {"title": "Unauthorized"})

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_429_is_retryable(self):
        publisher = TwitterPublisher()
        post = _make_post(content="hi")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(429, {"title": "Too Many Requests"})

            result = await publisher.publish(post, "token")

        assert result.success is False
        assert result.retryable is True


# ══════════════════════════════════════════════════════════════════════════════
# ERROR CLASSIFICATION (unit)
# ══════════════════════════════════════════════════════════════════════════════

class TestTwitterErrorClassification:
    def test_401_authentication(self):
        err = classify_twitter_error(401, {"title": "Unauthorized"})
        assert err.code == TwitterErrorCode.AUTHENTICATION_REQUIRED
        assert err.retryable is False

    def test_403_duplicate(self):
        err = classify_twitter_error(403, {"detail": "You are not allowed to create a duplicate Tweet."})
        assert err.code == TwitterErrorCode.DUPLICATE_CONTENT

    def test_403_access_tier(self):
        err = classify_twitter_error(403, {
            "detail": "Your client app is not configured with the appropriate access level (product)."
        })
        assert err.code == TwitterErrorCode.ACCESS_TIER_REQUIRED

    def test_403_generic_permission(self):
        err = classify_twitter_error(403, {"detail": "Forbidden"})
        assert err.code == TwitterErrorCode.PERMISSION_DENIED

    def test_429_rate_limited(self):
        err = classify_twitter_error(429, {"title": "Too Many Requests"})
        assert err.code == TwitterErrorCode.RATE_LIMITED
        assert err.retryable is True

    def test_402_credits_depleted(self):
        err = classify_twitter_error(402, {
            "detail": "credits depleted", "title": "Payment Required", "status": 402,
        })
        assert err.code == TwitterErrorCode.CREDITS_DEPLETED
        assert err.retryable is False
        assert "credit" in err.message.lower()

    def test_400_media(self):
        err = classify_twitter_error(400, {"detail": "Invalid media id"})
        assert err.code == TwitterErrorCode.INVALID_MEDIA

    def test_400_content(self):
        err = classify_twitter_error(400, {"detail": "Bad request"})
        assert err.code == TwitterErrorCode.INVALID_CONTENT

    def test_500_platform_unavailable(self):
        err = classify_twitter_error(503, {"title": "Service Unavailable"})
        assert err.code == TwitterErrorCode.PLATFORM_UNAVAILABLE
        assert err.retryable is True

    def test_oauth_error_shape(self):
        err = classify_twitter_error(400, {"error": "invalid_request", "error_description": "bad"})
        assert err.code == TwitterErrorCode.INVALID_CONTENT

    def test_token_not_leaked_in_message(self):
        err = classify_twitter_error(401, {"detail": "bad access_token abc123"})
        assert "abc123" not in err.message


# ══════════════════════════════════════════════════════════════════════════════
# MEDIA VALIDATION (unit)
# ══════════════════════════════════════════════════════════════════════════════

class TestTwitterMediaValidation:
    def test_detect_text(self):
        assert detect_media_type([]) == "text"

    def test_detect_image(self):
        assert detect_media_type(["https://cdn.example.com/a.jpg"]) == "image"

    def test_detect_video(self):
        assert detect_media_type(["https://cdn.example.com/a.mp4"]) == "video"

    def test_text_only_valid(self):
        assert validate_for_publishing("hello", []) == []

    def test_empty_invalid(self):
        assert validate_for_publishing("", []) != []
