"""Publishing scheduler — discovers due posts and creates jobs in the queue.

The scheduler is SEPARATE from the worker. Its only responsibility is:
  1. Find posts with status=SCHEDULED and scheduled_at <= now
  2. Atomically transition them to QUEUED
  3. Create a job in the job queue (with idempotency protection)
  4. Periodically recover stuck jobs (crash recovery)

The scheduler does NOT publish anything. The worker picks up jobs independently.

This separation means:
  - The scheduler and worker can run at different rates
  - Multiple workers can run concurrently (job claiming is atomic)
  - If the scheduler crashes, pending jobs still get processed
  - If a worker crashes, stuck-job recovery rescues the job
"""

import asyncio
from datetime import datetime, timezone

from app.social_publishing.domain.enums import PostStatus
from app.social_publishing.jobs.models import LOCK_TIMEOUT_SECONDS
from app.social_publishing.jobs.repository import JobRepository
from app.social_publishing.repositories.social_posts_repository import SocialPostsRepository
from app.services.logger import log

# How often to scan for due posts (seconds)
SCHEDULER_POLL_INTERVAL = 30

# How often to run stuck-job recovery (seconds)
RECOVERY_INTERVAL = 120

# Max posts to process per scheduler tick
BATCH_SIZE = 50


class JobScheduler:
    """Background worker that discovers due posts and enqueues jobs."""

    def __init__(
        self,
        posts_repo: SocialPostsRepository,
        job_repo: JobRepository,
        poll_interval: int = SCHEDULER_POLL_INTERVAL,
        recovery_interval: int = RECOVERY_INTERVAL,
        metrics: object = None,
        rate_limiter=None,
    ) -> None:
        self._posts = posts_repo
        self._jobs = job_repo
        self._poll_interval = poll_interval
        self._recovery_interval = recovery_interval
        self._metrics = metrics
        self._rate_limiter = rate_limiter
        self._task: asyncio.Task | None = None
        self._recovery_task: asyncio.Task | None = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._schedule_loop())
        self._recovery_task = asyncio.create_task(self._recovery_loop())
        log.info("JobScheduler started", poll_s=self._poll_interval, recovery_s=self._recovery_interval)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        if self._recovery_task:
            self._recovery_task.cancel()
            self._recovery_task = None
        log.info("JobScheduler stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ── Scheduling loop ───────────────────────────────────────────────────────

    async def _schedule_loop(self) -> None:
        while self._running:
            try:
                await self._process_due_posts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("JobScheduler schedule loop error", error=str(e))
            await asyncio.sleep(self._poll_interval)

    async def _process_due_posts(self) -> None:
        """Find SCHEDULED posts that are due and create jobs for them."""
        now = datetime.now(timezone.utc)
        due_posts = await self._posts.find_due_for_scheduling(now, limit=BATCH_SIZE)

        for post in due_posts:
            # Check rate limit before proceeding
            if self._rate_limiter:
                limit_check = await self._rate_limiter.check(post.tenant_id, post.platform.value)
                if not limit_check["allowed"]:
                    log.warning(
                        "Rate limit reached — skipping post",
                        post_id=post.id,
                        platform=post.platform.value,
                        resets_at=limit_check["resets_at"],
                    )
                    continue  # Leave in SCHEDULED state; will be picked up next day

            # Atomically transition post to QUEUED (prevents double-scheduling)
            updated = await self._posts.update_status_by_id(post.id, PostStatus.QUEUED)
            if not updated:
                continue  # Another scheduler/process already claimed it

            # Create a job with idempotency key (post_id ensures no duplicates)
            idempotency_key = f"publish_{post.id}_{post.retry_count}"
            job = await self._jobs.enqueue(
                tenant_id=post.tenant_id,
                post_id=post.id,
                account_id=post.account_id,
                platform=post.platform,
                idempotency_key=idempotency_key,
                scheduled_at=post.scheduled_at,
            )

            if job:
                # Increment rate limit counter
                if self._rate_limiter:
                    await self._rate_limiter.increment(post.tenant_id, post.platform.value)
                log.info(
                    "Job created for due post",
                    job_id=job.id,
                    post_id=post.id,
                    platform=post.platform.value,
                )
                if self._metrics:
                    self._metrics.record_job_created()
            else:
                # Idempotency key collision — job already exists for this post
                log.debug("Job already exists for post", post_id=post.id)

    # ── Recovery loop ─────────────────────────────────────────────────────────

    async def _recovery_loop(self) -> None:
        """Periodically release stuck jobs (crash recovery)."""
        while self._running:
            try:
                released = await self._jobs.release_stuck_jobs(LOCK_TIMEOUT_SECONDS)
                if released > 0:
                    log.warning("Released stuck jobs", count=released)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("JobScheduler recovery loop error", error=str(e))
            await asyncio.sleep(self._recovery_interval)
