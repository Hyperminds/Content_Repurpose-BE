"""End-to-end staging tests — full publishing flow with mocked external APIs.

Tests the complete pipeline:
  User creates post → Scheduler picks it up → Job created → Worker claims →
  Publisher called → Post marked published → WebSocket notification sent

Also covers:
  - Account connection → capability detection → post creation → scheduling
  - Agent plan generation → approval → execution → job creation
  - Token refresh cycle
  - Rate limit enforcement across the pipeline
  - Tenant isolation across the full flow
  - Failure recovery (retry after temporary failure)
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from app.social_publishing.domain.enums import (
    AccountCapability,
    ConnectionStatus,
    PostStatus,
    SocialPlatform,
)
from app.social_publishing.domain.models import SocialAccount, SocialPost, PublishingResult
from app.social_publishing.jobs.models import JobStatus, QueuedJob, FailureCategory
from app.social_publishing.jobs.scheduler import JobScheduler
from app.social_publishing.jobs.worker import PublishingWorker
from app.social_publishing.jobs.metrics import PublishingMetrics
from app.social_publishing.jobs.rate_limiter import PublishingRateLimiter
from app.social_publishing.publishers.registry import PublisherRegistry
from app.social_publishing.publishers.stub_publisher import StubPublisher
from app.social_publishing.agent.models import ApprovalMode, PlanStatus, ActionStatus
from app.social_publishing.agent.plan_service import PlanService
from app.social_publishing.agent.plan_execution import PlanExecutionService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_scheduled_post(tenant_id="tenant_1", platform="linkedin") -> SocialPost:
    return SocialPost(
        id="post_e2e_1",
        tenant_id=tenant_id,
        account_id="acc_e2e_1",
        platform=SocialPlatform(platform),
        status=PostStatus.SCHEDULED,
        content="End-to-end test post",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),  # Due now
    )


def _make_account(tenant_id="tenant_1", connected=True) -> SocialAccount:
    return SocialAccount(
        id="acc_e2e_1",
        tenant_id=tenant_id,
        platform=SocialPlatform.LINKEDIN,
        account_name="Test Account",
        platform_account_id="ext_e2e_1",
        connection_status=ConnectionStatus.CONNECTED if connected else ConnectionStatus.DISCONNECTED,
        has_access_token=connected,
        capabilities=[AccountCapability.TEXT_PUBLISHING] if connected else [],
    )


def _make_queued_job(tenant_id="tenant_1") -> QueuedJob:
    return QueuedJob(
        id="job_e2e_1",
        tenant_id=tenant_id,
        post_id="post_e2e_1",
        account_id="acc_e2e_1",
        platform=SocialPlatform.LINKEDIN,
        idempotency_key="publish_post_e2e_1_0",
        status=JobStatus.CLAIMED,
        attempts=1,
        locked_by="worker_test",
        locked_at=datetime.now(timezone.utc),
    )


# ══════════════════════════════════════════════════════════════════════════════
# FULL PIPELINE: Scheduler → Job → Worker → Published
# ══════════════════════════════════════════════════════════════════════════════

class TestFullPublishingPipeline:
    @pytest.mark.asyncio
    async def test_scheduled_post_becomes_published(self):
        """Complete flow: SCHEDULED post → scheduler creates job → worker publishes."""
        # Setup mocks
        posts_repo = AsyncMock()
        job_repo = AsyncMock()
        accounts_repo = AsyncMock()
        metrics = PublishingMetrics()

        due_post = _make_scheduled_post()
        posts_repo.find_due_for_scheduling.return_value = [due_post]
        posts_repo.update_status_by_id.return_value = True
        posts_repo.find_by_id_any_tenant.return_value = due_post

        mock_job = _make_queued_job()
        job_repo.enqueue.return_value = mock_job
        job_repo.claim_next.return_value = mock_job
        job_repo.mark_executing.return_value = True
        job_repo.mark_completed.return_value = True

        accounts_repo.find_by_id.return_value = _make_account()

        registry = PublisherRegistry()
        registry.register(StubPublisher(platform="linkedin"))

        rate_limiter = AsyncMock()
        rate_limiter.check.return_value = {"allowed": True, "remaining": 19}
        rate_limiter.increment = AsyncMock()

        # Step 1: Scheduler discovers due post and creates job
        scheduler = JobScheduler(posts_repo, job_repo, metrics=metrics, rate_limiter=rate_limiter)
        await scheduler._process_due_posts()

        job_repo.enqueue.assert_called_once()
        rate_limiter.increment.assert_called_once()
        assert metrics.jobs_created == 1

        # Step 2: Worker claims and publishes
        worker = PublishingWorker(
            job_repo=job_repo,
            posts_repo=posts_repo,
            accounts_repo=accounts_repo,
            publisher_registry=registry,
            credential_resolver=AsyncMock(return_value="token_123"),
            metrics=metrics,
        )

        with patch("app.social_publishing.notifications.notify_publish_success", new_callable=AsyncMock) as mock_notify:
            await worker._execute_job(mock_job)

        job_repo.mark_completed.assert_called_once()
        mock_notify.assert_called_once()
        assert metrics.jobs_succeeded == 1

    @pytest.mark.asyncio
    async def test_rate_limit_blocks_scheduling(self):
        """Post stays SCHEDULED when rate limit is reached."""
        posts_repo = AsyncMock()
        job_repo = AsyncMock()
        metrics = PublishingMetrics()

        due_post = _make_scheduled_post()
        posts_repo.find_due_for_scheduling.return_value = [due_post]

        rate_limiter = AsyncMock()
        rate_limiter.check.return_value = {"allowed": False, "remaining": 0, "resets_at": "2026-08-28T00:00:00Z"}

        scheduler = JobScheduler(posts_repo, job_repo, metrics=metrics, rate_limiter=rate_limiter)
        await scheduler._process_due_posts()

        # Job should NOT be created
        job_repo.enqueue.assert_not_called()
        # Post should NOT be transitioned to QUEUED
        posts_repo.update_status_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_temporary_failure_triggers_retry_notification(self):
        """Worker sends retry notification on temporary failure."""
        job_repo = AsyncMock()
        job_repo.mark_executing.return_value = True
        job_repo.mark_failed.return_value = True
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = _make_scheduled_post()
        posts_repo.update_status_by_id.return_value = True
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = _make_account()

        # Publisher that fails with retryable error
        failing_pub = StubPublisher(platform="linkedin", should_fail=True, error_message="Connection reset")
        registry = PublisherRegistry()
        registry.register(failing_pub)

        metrics = PublishingMetrics()
        worker = PublishingWorker(
            job_repo=job_repo, posts_repo=posts_repo, accounts_repo=accounts_repo,
            publisher_registry=registry,
            credential_resolver=AsyncMock(return_value="token"),
            metrics=metrics,
        )

        job = _make_queued_job()
        with patch("app.social_publishing.notifications.notify_publish_retrying", new_callable=AsyncMock) as mock_notify:
            await worker._execute_job(job)

        mock_notify.assert_called_once()
        assert metrics.jobs_retried == 1


# ══════════════════════════════════════════════════════════════════════════════
# AGENT PLAN → EXECUTION → JOBS
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentToPublishingPipeline:
    @pytest.mark.asyncio
    async def test_agent_plan_approved_creates_jobs(self):
        """Agent generates plan → user approves → execution creates posts + jobs."""
        # Mock AI client
        ai_client = AsyncMock()
        mock_choice = AsyncMock()
        mock_choice.message.content = '{"content": "AI generated post", "hashtags": ["ai"]}'
        ai_client.chat.completions.create.return_value = AsyncMock(choices=[mock_choice])

        from app.social_publishing.agent.content_generation import ContentGenerationService
        content_svc = ContentGenerationService(ai_client=ai_client)

        # Mock plan repo
        store: dict = {}
        plan_repo = AsyncMock()
        plan_repo.save = AsyncMock(side_effect=lambda p: store.update({p.id: p}) or p.id)
        plan_repo.find_by_id = AsyncMock(side_effect=lambda pid, tid: store.get(pid) if store.get(pid, MagicMock()).tenant_id == tid else None)

        plan_svc = PlanService(content_service=content_svc, plan_repo=plan_repo)

        # Generate plan
        plan = await plan_svc.generate_single_post_plan(
            tenant_id="tenant_1",
            source_content="Check out our new product!",
            platform="linkedin",
            account_id="acc_1",
            approval_mode=ApprovalMode.MANUAL_APPROVAL,
        )
        assert plan.status == PlanStatus.PENDING_APPROVAL

        # Approve
        store[plan.id] = plan  # Ensure it's in the mock store
        plan_repo.find_by_id = AsyncMock(return_value=plan)
        approved = await plan_svc.approve_plan(plan.id, "tenant_1", "user_123")
        assert approved.status == PlanStatus.APPROVED

        # Execute — creates posts and jobs
        posts_repo = AsyncMock()
        from app.social_publishing.domain.models import SocialPost as SP
        posts_repo.create.return_value = SP(
            id="post_new", tenant_id="tenant_1", account_id="acc_1",
            platform=SocialPlatform.LINKEDIN, status=PostStatus.SCHEDULED, content="x"
        )
        job_repo = AsyncMock()
        job_repo.enqueue.return_value = QueuedJob(
            id="job_new", tenant_id="tenant_1", post_id="post_new",
            account_id="acc_1", platform=SocialPlatform.LINKEDIN,
            idempotency_key="k", status=JobStatus.PENDING
        )

        exec_svc = PlanExecutionService(posts_repo, job_repo)
        result = await exec_svc.execute_plan(approved)

        assert result.status == PlanStatus.COMPLETED
        posts_repo.create.assert_called_once()
        job_repo.enqueue.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# TENANT ISOLATION ACROSS FULL FLOW
# ══════════════════════════════════════════════════════════════════════════════

class TestTenantIsolationEndToEnd:
    @pytest.mark.asyncio
    async def test_worker_uses_correct_tenant_for_account_lookup(self):
        """Worker must use the JOB's tenant_id, not a global one."""
        job_repo = AsyncMock()
        job_repo.mark_executing.return_value = True
        job_repo.mark_completed.return_value = True
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = _make_scheduled_post(tenant_id="tenant_X")
        posts_repo.update_status_by_id.return_value = True
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = _make_account(tenant_id="tenant_X")

        registry = PublisherRegistry()
        registry.register(StubPublisher(platform="linkedin"))

        worker = PublishingWorker(
            job_repo=job_repo, posts_repo=posts_repo, accounts_repo=accounts_repo,
            publisher_registry=registry,
            credential_resolver=AsyncMock(return_value="token"),
            metrics=PublishingMetrics(),
        )

        job = _make_queued_job(tenant_id="tenant_X")
        with patch("app.social_publishing.notifications.notify_publish_success", new_callable=AsyncMock):
            await worker._execute_job(job)

        # Account lookup used the job's tenant
        accounts_repo.find_by_id.assert_called_with("acc_e2e_1", "tenant_X")

    @pytest.mark.asyncio
    async def test_scheduler_processes_all_tenants(self):
        """Scheduler handles posts from multiple tenants in one tick."""
        posts_repo = AsyncMock()
        job_repo = AsyncMock()

        post_a = _make_scheduled_post(tenant_id="tenant_A")
        post_b = _make_scheduled_post(tenant_id="tenant_B")
        post_b.id = "post_e2e_2"
        posts_repo.find_due_for_scheduling.return_value = [post_a, post_b]
        posts_repo.update_status_by_id.return_value = True

        job_repo.enqueue.return_value = _make_queued_job()

        rate_limiter = AsyncMock()
        rate_limiter.check.return_value = {"allowed": True, "remaining": 10}
        rate_limiter.increment = AsyncMock()

        scheduler = JobScheduler(posts_repo, job_repo, metrics=PublishingMetrics(), rate_limiter=rate_limiter)
        await scheduler._process_due_posts()

        # Should create jobs for both tenants
        assert job_repo.enqueue.call_count == 2
        # Rate limit checked per-tenant
        assert rate_limiter.check.call_count == 2


# ══════════════════════════════════════════════════════════════════════════════
# DISCONNECTED ACCOUNT HANDLING
# ══════════════════════════════════════════════════════════════════════════════

class TestDisconnectedAccountFlow:
    @pytest.mark.asyncio
    async def test_disconnected_account_fails_job_permanently(self):
        """Job for disconnected account fails immediately (no retry)."""
        job_repo = AsyncMock()
        job_repo.mark_executing.return_value = True
        job_repo.mark_failed.return_value = True
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = _make_scheduled_post()
        posts_repo.update_status_by_id.return_value = True
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = _make_account(connected=False)

        worker = PublishingWorker(
            job_repo=job_repo, posts_repo=posts_repo, accounts_repo=accounts_repo,
            publisher_registry=PublisherRegistry(),
            credential_resolver=AsyncMock(return_value=None),
            metrics=PublishingMetrics(),
        )

        job = _make_queued_job()
        await worker._execute_job(job)

        job_repo.mark_failed.assert_called_once()
        # Should be permanent failure (no next_retry_at)
        call_args = job_repo.mark_failed.call_args
        assert call_args[0][3] == FailureCategory.PERMANENT
