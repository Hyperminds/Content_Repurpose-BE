"""Unit tests for service layer — state transitions, validation, tenant isolation.

These tests use mock repositories to avoid MongoDB dependency, verifying
pure business logic in isolation.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.social_publishing.domain.enums import PostStatus, SocialPlatform
from app.social_publishing.domain.exceptions import (
    AccountNotFound,
    InvalidStateTransition,
    PlatformNotSupported,
    PostNotFound,
    SchedulingError,
    ValidationError,
)
from app.social_publishing.domain.models import SocialAccount, SocialPost, PublishingResult
from app.social_publishing.services.social_accounts_service import SocialAccountsService
from app.social_publishing.services.social_posts_service import SocialPostsService
from app.social_publishing.services.publishing_orchestrator import PublishingOrchestrator
from app.social_publishing.publishers.registry import PublisherRegistry
from app.social_publishing.publishers.stub_publisher import StubPublisher


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_account(tenant_id: str = "tenant_1", platform: str = "linkedin") -> SocialAccount:
    return SocialAccount(
        id="acc_1",
        tenant_id=tenant_id,
        platform=SocialPlatform(platform),
        account_name="Test Account",
        platform_account_id="ext_123",
        is_active=True,
        has_access_token=True,
    )


def _make_post(
    tenant_id: str = "tenant_1",
    status: PostStatus = PostStatus.DRAFT,
    scheduled_at: datetime = None,
) -> SocialPost:
    return SocialPost(
        id="post_1",
        tenant_id=tenant_id,
        account_id="acc_1",
        platform=SocialPlatform.LINKEDIN,
        status=status,
        content="Test content",
        scheduled_at=scheduled_at,
        retry_count=0,
        max_retries=5,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ACCOUNTS SERVICE
# ══════════════════════════════════════════════════════════════════════════════

class TestSocialAccountsService:
    def _make_service(self, repo=None):
        repo = repo or AsyncMock()
        return SocialAccountsService(repo)

    @pytest.mark.asyncio
    async def test_create_account_validates_platform(self):
        svc = self._make_service()
        with pytest.raises(PlatformNotSupported):
            await svc.create_account("t1", "tiktok", "My TikTok", "ext_1")

    @pytest.mark.asyncio
    async def test_create_account_validates_name(self):
        svc = self._make_service()
        with pytest.raises(ValidationError):
            await svc.create_account("t1", "linkedin", "  ", "ext_1")

    @pytest.mark.asyncio
    async def test_create_account_validates_platform_id(self):
        svc = self._make_service()
        with pytest.raises(ValidationError):
            await svc.create_account("t1", "linkedin", "Name", "  ")

    @pytest.mark.asyncio
    async def test_create_account_success(self):
        repo = AsyncMock()
        repo.create.return_value = _make_account()
        svc = self._make_service(repo)
        result = await svc.create_account("tenant_1", "linkedin", "My LinkedIn", "ext_123")
        assert result.platform == SocialPlatform.LINKEDIN
        repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_account_not_found(self):
        repo = AsyncMock()
        repo.find_by_id.return_value = None
        svc = self._make_service(repo)
        with pytest.raises(AccountNotFound):
            await svc.get_account("nonexistent", "tenant_1")

    @pytest.mark.asyncio
    async def test_get_account_enforces_tenant(self):
        repo = AsyncMock()
        repo.find_by_id.return_value = None  # tenant mismatch = not found
        svc = self._make_service(repo)
        with pytest.raises(AccountNotFound):
            await svc.get_account("acc_1", "wrong_tenant")
        repo.find_by_id.assert_called_with("acc_1", "wrong_tenant")


# ══════════════════════════════════════════════════════════════════════════════
# POSTS SERVICE
# ══════════════════════════════════════════════════════════════════════════════

class TestSocialPostsService:
    def _make_service(self, posts_repo=None, accounts_repo=None):
        posts_repo = posts_repo or AsyncMock()
        accounts_repo = accounts_repo or AsyncMock()
        return SocialPostsService(posts_repo, accounts_repo)

    @pytest.mark.asyncio
    async def test_create_post_validates_platform(self):
        svc = self._make_service()
        with pytest.raises(PlatformNotSupported):
            await svc.create_post("t1", "acc_1", "fakebook", "Hello")

    @pytest.mark.asyncio
    async def test_create_post_validates_empty_content(self):
        svc = self._make_service()
        with pytest.raises(ValidationError):
            await svc.create_post("t1", "acc_1", "linkedin", "   ")

    @pytest.mark.asyncio
    async def test_create_post_validates_account_ownership(self):
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = None  # account not found for tenant
        svc = self._make_service(accounts_repo=accounts_repo)
        with pytest.raises(AccountNotFound):
            await svc.create_post("t1", "acc_1", "linkedin", "Hello")

    @pytest.mark.asyncio
    async def test_create_post_with_schedule(self):
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = _make_account()
        posts_repo = AsyncMock()
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        expected_post = _make_post(status=PostStatus.SCHEDULED, scheduled_at=future)
        posts_repo.create.return_value = expected_post

        svc = self._make_service(posts_repo, accounts_repo)
        result = await svc.create_post("tenant_1", "acc_1", "linkedin", "Hello", scheduled_at=future)
        assert result.status == PostStatus.SCHEDULED

    @pytest.mark.asyncio
    async def test_create_post_rejects_past_schedule(self):
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = _make_account()
        svc = self._make_service(accounts_repo=accounts_repo)
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        with pytest.raises(SchedulingError):
            await svc.create_post("t1", "acc_1", "linkedin", "Hello", scheduled_at=past)

    @pytest.mark.asyncio
    async def test_schedule_post_from_draft(self):
        posts_repo = AsyncMock()
        post = _make_post(status=PostStatus.DRAFT)
        posts_repo.find_by_id.return_value = post
        scheduled_post = _make_post(status=PostStatus.SCHEDULED)
        posts_repo.update_status.return_value = scheduled_post

        svc = self._make_service(posts_repo)
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        result = await svc.schedule_post("post_1", "tenant_1", future)
        assert result.status == PostStatus.SCHEDULED

    @pytest.mark.asyncio
    async def test_schedule_post_rejects_from_published(self):
        posts_repo = AsyncMock()
        post = _make_post(status=PostStatus.PUBLISHED)
        posts_repo.find_by_id.return_value = post

        svc = self._make_service(posts_repo)
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        with pytest.raises(InvalidStateTransition):
            await svc.schedule_post("post_1", "tenant_1", future)

    @pytest.mark.asyncio
    async def test_cancel_from_scheduled(self):
        posts_repo = AsyncMock()
        post = _make_post(status=PostStatus.SCHEDULED)
        posts_repo.find_by_id.return_value = post
        cancelled = _make_post(status=PostStatus.CANCELLED)
        posts_repo.update_status.return_value = cancelled

        svc = self._make_service(posts_repo)
        result = await svc.cancel_post("post_1", "tenant_1")
        assert result.status == PostStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_from_published_fails(self):
        posts_repo = AsyncMock()
        post = _make_post(status=PostStatus.PUBLISHED)
        posts_repo.find_by_id.return_value = post

        svc = self._make_service(posts_repo)
        with pytest.raises(InvalidStateTransition):
            await svc.cancel_post("post_1", "tenant_1")

    @pytest.mark.asyncio
    async def test_update_only_draft_or_scheduled(self):
        posts_repo = AsyncMock()
        post = _make_post(status=PostStatus.PUBLISHING)
        posts_repo.find_by_id.return_value = post

        svc = self._make_service(posts_repo)
        with pytest.raises(InvalidStateTransition):
            await svc.update_post("post_1", "tenant_1", content="New content")

    @pytest.mark.asyncio
    async def test_delete_only_draft_or_cancelled(self):
        posts_repo = AsyncMock()
        post = _make_post(status=PostStatus.SCHEDULED)
        posts_repo.find_by_id.return_value = post

        svc = self._make_service(posts_repo)
        with pytest.raises(InvalidStateTransition):
            await svc.delete_post("post_1", "tenant_1")

    @pytest.mark.asyncio
    async def test_get_post_not_found(self):
        posts_repo = AsyncMock()
        posts_repo.find_by_id.return_value = None
        svc = self._make_service(posts_repo)
        with pytest.raises(PostNotFound):
            await svc.get_post("missing_id", "tenant_1")


# ══════════════════════════════════════════════════════════════════════════════
# PUBLISHING ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

class TestPublishingOrchestrator:
    def _make_orchestrator(
        self,
        posts_repo=None,
        jobs_repo=None,
        registry=None,
        token_resolver=None,
    ):
        posts_repo = posts_repo or AsyncMock()
        jobs_repo = jobs_repo or AsyncMock()
        registry = registry or PublisherRegistry()
        return PublishingOrchestrator(posts_repo, jobs_repo, registry, token_resolver)

    @pytest.mark.asyncio
    async def test_publish_success(self):
        post = _make_post(status=PostStatus.QUEUED)
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = post
        posts_repo.update_status_by_id.return_value = True

        jobs_repo = AsyncMock()
        from app.social_publishing.domain.models import PublishingJob
        jobs_repo.create.return_value = PublishingJob(
            id="job_1", tenant_id="tenant_1", post_id="post_1",
            platform=SocialPlatform.LINKEDIN, status=PostStatus.PUBLISHING,
        )

        registry = PublisherRegistry()
        registry.register(StubPublisher(platform="linkedin"))

        async def mock_token(acc_id, t_id):
            return "valid_token"

        orch = self._make_orchestrator(posts_repo, jobs_repo, registry, mock_token)
        result = await orch.publish_post("post_1")
        assert result.success is True
        assert result.platform_post_id is not None

    @pytest.mark.asyncio
    async def test_publish_fails_no_publisher(self):
        post = _make_post(status=PostStatus.QUEUED)
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = post
        posts_repo.update_status_by_id.return_value = True

        orch = self._make_orchestrator(posts_repo=posts_repo)
        result = await orch.publish_post("post_1")
        assert result.success is False
        assert "No publisher" in result.error_message

    @pytest.mark.asyncio
    async def test_publish_fails_no_token(self):
        post = _make_post(status=PostStatus.QUEUED)
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = post
        posts_repo.update_status_by_id.return_value = True

        registry = PublisherRegistry()
        registry.register(StubPublisher(platform="linkedin"))

        async def no_token(acc_id, t_id):
            return None

        orch = self._make_orchestrator(posts_repo, registry=registry, token_resolver=no_token)
        result = await orch.publish_post("post_1")
        assert result.success is False
        assert "token" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_publish_retries_on_retryable_failure(self):
        post = _make_post(status=PostStatus.QUEUED)
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = post
        posts_repo.update_status_by_id.return_value = True

        jobs_repo = AsyncMock()
        from app.social_publishing.domain.models import PublishingJob
        jobs_repo.create.return_value = PublishingJob(
            id="job_1", tenant_id="tenant_1", post_id="post_1",
            platform=SocialPlatform.LINKEDIN, status=PostStatus.PUBLISHING,
        )

        registry = PublisherRegistry()
        registry.register(StubPublisher(platform="linkedin", should_fail=True, retryable=True))

        async def mock_token(acc_id, t_id):
            return "token"

        orch = self._make_orchestrator(posts_repo, jobs_repo, registry, mock_token)
        result = await orch.publish_post("post_1")
        assert result.success is False

        # Should have called update_status_by_id with RETRYING
        calls = posts_repo.update_status_by_id.call_args_list
        retrying_call = [c for c in calls if c.args[1] == PostStatus.RETRYING]
        assert len(retrying_call) == 1

    @pytest.mark.asyncio
    async def test_publish_post_not_found(self):
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = None

        orch = self._make_orchestrator(posts_repo=posts_repo)
        with pytest.raises(PostNotFound):
            await orch.publish_post("nonexistent")

    @pytest.mark.asyncio
    async def test_publish_wrong_state(self):
        post = _make_post(status=PostStatus.DRAFT)  # not QUEUED
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = post

        orch = self._make_orchestrator(posts_repo=posts_repo)
        result = await orch.publish_post("post_1")
        assert result.success is False
        assert "non-publishable" in result.error_message.lower()
