"""Unit tests for the social publishing scheduler."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from app.social_publishing.domain.enums import PostStatus, SocialPlatform
from app.social_publishing.domain.models import SocialPost
from app.social_publishing.scheduling import SocialPublishingScheduler


def _make_due_post(post_id: str = "post_1") -> SocialPost:
    return SocialPost(
        id=post_id,
        tenant_id="tenant_1",
        account_id="acc_1",
        platform=SocialPlatform.LINKEDIN,
        status=PostStatus.SCHEDULED,
        content="Due post",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )


def _make_retryable_post(post_id: str = "post_2") -> SocialPost:
    return SocialPost(
        id=post_id,
        tenant_id="tenant_1",
        account_id="acc_1",
        platform=SocialPlatform.TWITTER,
        status=PostStatus.FAILED,
        content="Retryable",
        retry_count=1,
        max_retries=5,
    )


class TestSocialPublishingScheduler:
    def _make_scheduler(self, posts_repo=None, orchestrator=None):
        posts_repo = posts_repo or AsyncMock()
        orchestrator = orchestrator or AsyncMock()
        return SocialPublishingScheduler(posts_repo, orchestrator, poll_interval=1)

    @pytest.mark.asyncio
    async def test_start_stop(self):
        scheduler = self._make_scheduler()
        assert scheduler.running is False
        scheduler.start()
        assert scheduler.running is True
        scheduler.stop()
        assert scheduler.running is False

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        scheduler = self._make_scheduler()
        scheduler.start()
        scheduler.start()  # should not error
        assert scheduler.running is True
        scheduler.stop()

    @pytest.mark.asyncio
    async def test_process_due_posts(self):
        posts_repo = AsyncMock()
        orchestrator = AsyncMock()
        due_post = _make_due_post()
        posts_repo.find_due_for_scheduling.return_value = [due_post]
        posts_repo.update_status_by_id.return_value = True

        scheduler = self._make_scheduler(posts_repo, orchestrator)
        await scheduler._process_due_posts()

        posts_repo.update_status_by_id.assert_called_with(due_post.id, PostStatus.QUEUED)

    @pytest.mark.asyncio
    async def test_process_retryable_posts(self):
        posts_repo = AsyncMock()
        orchestrator = AsyncMock()
        retryable = _make_retryable_post()
        posts_repo.find_retryable.return_value = [retryable]
        posts_repo.update_status_by_id.return_value = True

        scheduler = self._make_scheduler(posts_repo, orchestrator)
        await scheduler._process_retryable_posts()

        posts_repo.update_status_by_id.assert_called_with(retryable.id, PostStatus.QUEUED)

    @pytest.mark.asyncio
    async def test_process_due_skips_if_no_posts(self):
        posts_repo = AsyncMock()
        posts_repo.find_due_for_scheduling.return_value = []
        orchestrator = AsyncMock()

        scheduler = self._make_scheduler(posts_repo, orchestrator)
        await scheduler._process_due_posts()

        posts_repo.update_status_by_id.assert_not_called()
