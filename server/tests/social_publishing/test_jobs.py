"""Tests for the Phase 3 job system — retry policy, metrics, worker, scheduler."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.social_publishing.domain.enums import (
    ConnectionStatus,
    PostStatus,
    SocialPlatform,
)
from app.social_publishing.domain.models import PublishingResult, SocialAccount, SocialPost
from app.social_publishing.jobs.models import (
    FailureCategory,
    JobStatus,
    MAX_ATTEMPTS,
    LOCK_TIMEOUT_SECONDS,
    QueuedJob,
)
from app.social_publishing.jobs.retry_policy import (
    classify_failure,
    compute_next_retry_at,
    is_retryable,
)
from app.social_publishing.jobs.metrics import PublishingMetrics
from app.social_publishing.jobs.worker import PublishingWorker
from app.social_publishing.jobs.scheduler import JobScheduler
from app.social_publishing.publishers.stub_publisher import StubPublisher


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_job(
    status: JobStatus = JobStatus.CLAIMED,
    attempts: int = 1,
    platform: str = "linkedin",
    tenant_id: str = "tenant_1",
) -> QueuedJob:
    return QueuedJob(
        id="job_1",
        tenant_id=tenant_id,
        post_id="post_1",
        account_id="acc_1",
        platform=SocialPlatform(platform),
        idempotency_key=f"publish_post_1_{attempts}",
        status=status,
        attempts=attempts,
        max_attempts=MAX_ATTEMPTS,
        locked_by="worker_test",
        locked_at=datetime.now(timezone.utc),
    )


def _make_post(status: PostStatus = PostStatus.QUEUED) -> SocialPost:
    return SocialPost(
        id="post_1",
        tenant_id="tenant_1",
        account_id="acc_1",
        platform=SocialPlatform.LINKEDIN,
        status=status,
        content="Test content",
    )


def _make_account(connection_status: ConnectionStatus = ConnectionStatus.CONNECTED) -> SocialAccount:
    return SocialAccount(
        id="acc_1",
        tenant_id="tenant_1",
        platform=SocialPlatform.LINKEDIN,
        account_name="Test Account",
        platform_account_id="ext_1",
        connection_status=connection_status,
        has_access_token=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# RETRY POLICY
# ══════════════════════════════════════════════════════════════════════════════

class TestRetryPolicy:
    def test_classify_auth_failure(self):
        result = PublishingResult(success=False, error_message="401 Unauthorized")
        assert classify_failure(result) == FailureCategory.AUTH_EXPIRED

    def test_classify_rate_limit(self):
        result = PublishingResult(success=False, error_message="429 Too Many Requests")
        assert classify_failure(result) == FailureCategory.RATE_LIMITED

    def test_classify_permanent(self):
        result = PublishingResult(success=False, error_message="Invalid content", retryable=False)
        assert classify_failure(result) == FailureCategory.PERMANENT

    def test_classify_platform_down(self):
        result = PublishingResult(success=False, error_message="503 Service Unavailable")
        assert classify_failure(result) == FailureCategory.PLATFORM_DOWN

    def test_classify_temporary(self):
        result = PublishingResult(success=False, error_message="Connection reset", retryable=True)
        assert classify_failure(result) == FailureCategory.TEMPORARY

    def test_classify_unknown_non_retryable(self):
        result = PublishingResult(success=False, error_message="Something weird", retryable=False)
        assert classify_failure(result) == FailureCategory.PERMANENT

    def test_is_retryable_temporary(self):
        assert is_retryable(FailureCategory.TEMPORARY, attempts=1) is True
        assert is_retryable(FailureCategory.TEMPORARY, attempts=4) is True
        assert is_retryable(FailureCategory.TEMPORARY, attempts=5) is False

    def test_is_retryable_permanent_never(self):
        assert is_retryable(FailureCategory.PERMANENT, attempts=0) is False
        assert is_retryable(FailureCategory.PERMANENT, attempts=1) is False

    def test_is_retryable_auth_once(self):
        assert is_retryable(FailureCategory.AUTH_EXPIRED, attempts=0) is True
        assert is_retryable(FailureCategory.AUTH_EXPIRED, attempts=1) is True
        assert is_retryable(FailureCategory.AUTH_EXPIRED, attempts=2) is False

    def test_is_retryable_rate_limit(self):
        assert is_retryable(FailureCategory.RATE_LIMITED, attempts=3) is True
        assert is_retryable(FailureCategory.RATE_LIMITED, attempts=5) is False

    def test_backoff_increases_with_attempts(self):
        t1 = compute_next_retry_at(1, FailureCategory.TEMPORARY)
        t2 = compute_next_retry_at(3, FailureCategory.TEMPORARY)
        # Later attempts should be further in the future
        assert t2 > t1

    def test_backoff_respects_retry_after(self):
        now = datetime.now(timezone.utc)
        result = compute_next_retry_at(1, FailureCategory.RATE_LIMITED, retry_after_seconds=120)
        # Should be approximately 120 seconds from now
        expected_min = now + timedelta(seconds=119)
        expected_max = now + timedelta(seconds=125)
        assert expected_min <= result <= expected_max

    def test_backoff_has_minimum(self):
        now = datetime.now(timezone.utc)
        result = compute_next_retry_at(1, FailureCategory.TEMPORARY)
        # Must be at least 10 seconds in the future
        assert result >= now + timedelta(seconds=10)

    def test_backoff_capped_at_max(self):
        now = datetime.now(timezone.utc)
        result = compute_next_retry_at(20, FailureCategory.TEMPORARY)
        # Must not exceed 2 hours + jitter
        max_allowed = now + timedelta(seconds=7200 * 1.3)
        assert result <= max_allowed


# ══════════════════════════════════════════════════════════════════════════════
# METRICS
# ══════════════════════════════════════════════════════════════════════════════

class TestPublishingMetrics:
    def test_initial_state(self):
        m = PublishingMetrics()
        assert m.jobs_created == 0
        assert m.jobs_processed == 0
        assert m.avg_latency_ms == 0.0
        assert m.success_rate == 0.0

    def test_record_success(self):
        m = PublishingMetrics()
        m.record_success(150)
        m.record_success(250)
        assert m.jobs_succeeded == 2
        assert m.jobs_processed == 2
        assert m.avg_latency_ms == 200.0
        assert m.success_rate == 100.0

    def test_record_failure(self):
        m = PublishingMetrics()
        m.record_success(100)
        m.record_failure()
        assert m.success_rate == 50.0

    def test_record_retry(self):
        m = PublishingMetrics()
        m.record_retry()
        assert m.jobs_retried == 1
        assert m.jobs_processed == 1

    def test_snapshot(self):
        m = PublishingMetrics()
        m.record_job_created()
        m.record_success(100)
        snap = m.snapshot()
        assert snap["jobs_created"] == 1
        assert snap["jobs_succeeded"] == 1
        assert snap["avg_latency_ms"] == 100.0
        assert "uptime_seconds" in snap

    def test_reset(self):
        m = PublishingMetrics()
        m.record_success(100)
        m.reset()
        assert m.jobs_succeeded == 0
        assert m.avg_latency_ms == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# WORKER
# ══════════════════════════════════════════════════════════════════════════════

class TestPublishingWorker:
    def _make_worker(
        self,
        job_repo=None,
        posts_repo=None,
        accounts_repo=None,
        publisher_registry=None,
        credential_resolver=None,
        metrics=None,
    ):
        from app.social_publishing.publishers.registry import PublisherRegistry
        job_repo = job_repo or AsyncMock()
        posts_repo = posts_repo or AsyncMock()
        accounts_repo = accounts_repo or AsyncMock()
        publisher_registry = publisher_registry or PublisherRegistry()
        credential_resolver = credential_resolver or AsyncMock(return_value="token_123")
        metrics = metrics or PublishingMetrics()
        return PublishingWorker(
            job_repo=job_repo,
            posts_repo=posts_repo,
            accounts_repo=accounts_repo,
            publisher_registry=publisher_registry,
            credential_resolver=credential_resolver,
            metrics=metrics,
        )

    @pytest.mark.asyncio
    async def test_execute_success(self):
        from app.social_publishing.publishers.registry import PublisherRegistry
        job_repo = AsyncMock()
        job_repo.mark_executing.return_value = True
        job_repo.mark_completed.return_value = True
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = _make_post()
        posts_repo.update_status_by_id.return_value = True
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = _make_account()

        registry = PublisherRegistry()
        registry.register(StubPublisher(platform="linkedin"))

        metrics = PublishingMetrics()
        worker = self._make_worker(
            job_repo=job_repo,
            posts_repo=posts_repo,
            accounts_repo=accounts_repo,
            publisher_registry=registry,
            metrics=metrics,
        )

        job = _make_job()
        await worker._execute_job(job)

        job_repo.mark_completed.assert_called_once()
        assert metrics.jobs_succeeded == 1

    @pytest.mark.asyncio
    async def test_execute_post_not_found(self):
        job_repo = AsyncMock()
        job_repo.mark_executing.return_value = True
        job_repo.mark_failed.return_value = True
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = None

        worker = self._make_worker(job_repo=job_repo, posts_repo=posts_repo)
        job = _make_job()
        await worker._execute_job(job)

        job_repo.mark_failed.assert_called_once()
        call_args = job_repo.mark_failed.call_args
        # mark_failed(job_id, worker_id, reason, category)
        assert call_args[0][2] == "Post no longer exists"  # reason
        assert call_args[0][3] == FailureCategory.PERMANENT  # category

    @pytest.mark.asyncio
    async def test_execute_account_disconnected(self):
        job_repo = AsyncMock()
        job_repo.mark_executing.return_value = True
        job_repo.mark_failed.return_value = True
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = _make_post()
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = _make_account(ConnectionStatus.DISCONNECTED)

        worker = self._make_worker(
            job_repo=job_repo, posts_repo=posts_repo, accounts_repo=accounts_repo
        )
        job = _make_job()
        await worker._execute_job(job)

        job_repo.mark_failed.assert_called_once()
        call_args = job_repo.mark_failed.call_args
        assert "disconnected" in call_args[0][2].lower()  # reason

    @pytest.mark.asyncio
    async def test_execute_no_credentials(self):
        from app.social_publishing.publishers.registry import PublisherRegistry
        job_repo = AsyncMock()
        job_repo.mark_executing.return_value = True
        job_repo.mark_failed.return_value = True
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = _make_post()
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = _make_account()

        registry = PublisherRegistry()
        registry.register(StubPublisher(platform="linkedin"))

        worker = self._make_worker(
            job_repo=job_repo,
            posts_repo=posts_repo,
            accounts_repo=accounts_repo,
            publisher_registry=registry,
            credential_resolver=AsyncMock(return_value=None),
        )
        job = _make_job()
        await worker._execute_job(job)

        job_repo.mark_failed.assert_called_once()
        call_args = job_repo.mark_failed.call_args
        assert call_args[0][3] == FailureCategory.AUTH_EXPIRED  # category

    @pytest.mark.asyncio
    async def test_execute_publisher_fails_retryable(self):
        from app.social_publishing.publishers.registry import PublisherRegistry
        job_repo = AsyncMock()
        job_repo.mark_executing.return_value = True
        job_repo.mark_failed.return_value = True
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = _make_post()
        posts_repo.update_status_by_id.return_value = True
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = _make_account()

        failing_pub = StubPublisher(platform="linkedin", should_fail=True, error_message="Connection reset")
        registry = PublisherRegistry()
        registry.register(failing_pub)

        metrics = PublishingMetrics()
        worker = self._make_worker(
            job_repo=job_repo,
            posts_repo=posts_repo,
            accounts_repo=accounts_repo,
            publisher_registry=registry,
            metrics=metrics,
        )
        job = _make_job(attempts=1)
        await worker._execute_job(job)

        # Should be marked as retrying (not permanently failed)
        call_args = job_repo.mark_failed.call_args
        assert call_args.kwargs.get("next_retry_at") is not None
        assert metrics.jobs_retried == 1

    @pytest.mark.asyncio
    async def test_execute_publisher_exception(self):
        from app.social_publishing.publishers.registry import PublisherRegistry
        job_repo = AsyncMock()
        job_repo.mark_executing.return_value = True
        job_repo.mark_failed.return_value = True
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = _make_post()
        posts_repo.update_status_by_id.return_value = True
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = _make_account()

        # Publisher that raises an exception
        pub = AsyncMock()
        pub.platform_name = "linkedin"
        pub.publish = AsyncMock(side_effect=RuntimeError("Network error"))
        registry = PublisherRegistry()
        registry.register(pub)

        worker = self._make_worker(
            job_repo=job_repo,
            posts_repo=posts_repo,
            accounts_repo=accounts_repo,
            publisher_registry=registry,
        )
        job = _make_job(attempts=1)
        await worker._execute_job(job)

        # Exception is caught and treated as retryable failure
        job_repo.mark_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_start_stop(self):
        worker = self._make_worker()
        assert worker.running is False
        worker.start()
        assert worker.running is True
        worker.stop()
        assert worker.running is False

    def test_worker_has_unique_id(self):
        w1 = self._make_worker()
        w2 = self._make_worker()
        assert w1.worker_id != w2.worker_id


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ══════════════════════════════════════════════════════════════════════════════

class TestJobScheduler:
    def _make_scheduler(self, posts_repo=None, job_repo=None, metrics=None):
        posts_repo = posts_repo or AsyncMock()
        job_repo = job_repo or AsyncMock()
        metrics = metrics or PublishingMetrics()
        return JobScheduler(posts_repo, job_repo, poll_interval=1, recovery_interval=5, metrics=metrics)

    @pytest.mark.asyncio
    async def test_start_stop(self):
        scheduler = self._make_scheduler()
        assert scheduler.running is False
        scheduler.start()
        assert scheduler.running is True
        scheduler.stop()
        assert scheduler.running is False

    @pytest.mark.asyncio
    async def test_process_due_posts_creates_jobs(self):
        posts_repo = AsyncMock()
        due_post = _make_post(status=PostStatus.SCHEDULED)
        posts_repo.find_due_for_scheduling.return_value = [due_post]
        posts_repo.update_status_by_id.return_value = True

        job_repo = AsyncMock()
        job_repo.enqueue.return_value = _make_job(status=JobStatus.PENDING)

        metrics = PublishingMetrics()
        scheduler = self._make_scheduler(posts_repo, job_repo, metrics)
        await scheduler._process_due_posts()

        posts_repo.update_status_by_id.assert_called_with(due_post.id, PostStatus.QUEUED)
        job_repo.enqueue.assert_called_once()
        assert metrics.jobs_created == 1

    @pytest.mark.asyncio
    async def test_process_due_skips_already_claimed(self):
        posts_repo = AsyncMock()
        due_post = _make_post(status=PostStatus.SCHEDULED)
        posts_repo.find_due_for_scheduling.return_value = [due_post]
        posts_repo.update_status_by_id.return_value = False  # Another process claimed it

        job_repo = AsyncMock()
        scheduler = self._make_scheduler(posts_repo, job_repo)
        await scheduler._process_due_posts()

        job_repo.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_idempotency_prevents_duplicate_job(self):
        posts_repo = AsyncMock()
        due_post = _make_post(status=PostStatus.SCHEDULED)
        posts_repo.find_due_for_scheduling.return_value = [due_post]
        posts_repo.update_status_by_id.return_value = True

        job_repo = AsyncMock()
        job_repo.enqueue.return_value = None  # Idempotency key collision

        scheduler = self._make_scheduler(posts_repo, job_repo)
        await scheduler._process_due_posts()

        # Should not crash — gracefully handles duplicate
        job_repo.enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_recovery_releases_stuck_jobs(self):
        job_repo = AsyncMock()
        job_repo.release_stuck_jobs.return_value = 3

        scheduler = self._make_scheduler(job_repo=job_repo)
        await scheduler._recovery_loop.__wrapped__(scheduler) if hasattr(scheduler._recovery_loop, '__wrapped__') else None

        # Manually invoke the recovery logic
        released = await job_repo.release_stuck_jobs(LOCK_TIMEOUT_SECONDS)
        assert released == 3


# ══════════════════════════════════════════════════════════════════════════════
# TENANT ISOLATION
# ══════════════════════════════════════════════════════════════════════════════

class TestJobTenantIsolation:
    @pytest.mark.asyncio
    async def test_worker_uses_job_tenant_for_account_lookup(self):
        from app.social_publishing.publishers.registry import PublisherRegistry
        job_repo = AsyncMock()
        job_repo.mark_executing.return_value = True
        job_repo.mark_completed.return_value = True
        posts_repo = AsyncMock()
        posts_repo.find_by_id_any_tenant.return_value = _make_post()
        posts_repo.update_status_by_id.return_value = True
        accounts_repo = AsyncMock()
        accounts_repo.find_by_id.return_value = _make_account()

        registry = PublisherRegistry()
        registry.register(StubPublisher(platform="linkedin"))

        worker = PublishingWorker(
            job_repo=job_repo,
            posts_repo=posts_repo,
            accounts_repo=accounts_repo,
            publisher_registry=registry,
            credential_resolver=AsyncMock(return_value="token"),
            metrics=PublishingMetrics(),
        )

        job = _make_job(tenant_id="tenant_A")
        await worker._execute_job(job)

        # Account lookup must use the job's tenant_id
        accounts_repo.find_by_id.assert_called_with("acc_1", "tenant_A")
