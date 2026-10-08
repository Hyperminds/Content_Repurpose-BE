"""Unit tests for the publisher protocol and registry."""

import pytest

from app.social_publishing.domain.enums import SocialPlatform, PostStatus
from app.social_publishing.domain.exceptions import PlatformNotSupported
from app.social_publishing.domain.models import SocialPost, PublishingResult
from app.social_publishing.publishers.base import SocialPublisher
from app.social_publishing.publishers.registry import PublisherRegistry
from app.social_publishing.publishers.stub_publisher import StubPublisher


def _make_post(content: str = "Hello world") -> SocialPost:
    return SocialPost(
        id="post_1",
        tenant_id="tenant_1",
        account_id="acc_1",
        platform=SocialPlatform.LINKEDIN,
        status=PostStatus.QUEUED,
        content=content,
    )


class TestStubPublisher:
    @pytest.mark.asyncio
    async def test_publish_success(self):
        pub = StubPublisher(platform="linkedin")
        post = _make_post()
        result = await pub.publish(post, "token_abc")
        assert result.success is True
        assert result.platform_post_id is not None

    @pytest.mark.asyncio
    async def test_publish_failure(self):
        pub = StubPublisher(platform="linkedin", should_fail=True, error_message="Rate limit")
        post = _make_post()
        result = await pub.publish(post, "token_abc")
        assert result.success is False
        assert result.error_message == "Rate limit"

    @pytest.mark.asyncio
    async def test_records_calls(self):
        pub = StubPublisher(platform="linkedin")
        post = _make_post()
        await pub.publish(post, "t1")
        await pub.publish(post, "t2")
        assert len(pub.publish_calls) == 2

    @pytest.mark.asyncio
    async def test_validate_empty_content(self):
        pub = StubPublisher(platform="linkedin")
        post = _make_post(content="   ")
        errors = await pub.validate_content(post)
        assert len(errors) > 0

    @pytest.mark.asyncio
    async def test_validate_valid_content(self):
        pub = StubPublisher(platform="linkedin")
        post = _make_post(content="Valid content")
        errors = await pub.validate_content(post)
        assert errors == []

    def test_satisfies_protocol(self):
        pub = StubPublisher(platform="test")
        assert isinstance(pub, SocialPublisher)

    def test_platform_name(self):
        pub = StubPublisher(platform="instagram")
        assert pub.platform_name == "instagram"


class TestPublisherRegistry:
    def test_register_and_get(self):
        registry = PublisherRegistry()
        pub = StubPublisher(platform="linkedin")
        registry.register(pub)
        assert registry.get("linkedin") is pub

    def test_get_with_enum(self):
        registry = PublisherRegistry()
        pub = StubPublisher(platform="linkedin")
        registry.register(pub)
        assert registry.get(SocialPlatform.LINKEDIN) is pub

    def test_get_raises_for_missing(self):
        registry = PublisherRegistry()
        with pytest.raises(PlatformNotSupported):
            registry.get("tiktok")

    def test_get_optional_returns_none(self):
        registry = PublisherRegistry()
        assert registry.get_optional("tiktok") is None

    def test_has(self):
        registry = PublisherRegistry()
        pub = StubPublisher(platform="reddit")
        registry.register(pub)
        assert registry.has("reddit") is True
        assert registry.has("tiktok") is False

    def test_registered_platforms(self):
        registry = PublisherRegistry()
        registry.register(StubPublisher(platform="linkedin"))
        registry.register(StubPublisher(platform="twitter"))
        platforms = registry.registered_platforms
        assert "linkedin" in platforms
        assert "twitter" in platforms
        assert len(platforms) == 2

    def test_register_replaces(self):
        registry = PublisherRegistry()
        pub1 = StubPublisher(platform="linkedin")
        pub2 = StubPublisher(platform="linkedin", should_fail=True)
        registry.register(pub1)
        registry.register(pub2)
        assert registry.get("linkedin") is pub2
