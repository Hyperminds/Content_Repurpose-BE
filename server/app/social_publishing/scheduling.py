"""Publishing scheduler — background worker that discovers due posts and queues
them for the orchestrator.

Follows the same asyncio background-task pattern used by the existing
scheduler_worker.py: start()/stop() lifecycle, periodic polling loop.

Responsibilities:
  1. Find SCHEDULED posts whose scheduled_at <= now → transition to QUEUED
  2. Find RETRYING posts eligible for another attempt → transition to QUEUED
  3. Fire the orchestrator for each queued post (async, non-blocking)

The scheduler never publishes directly — it only transitions state and hands
off to the orchestrator.
"""

import asyncio
from datetime import datetime, timezone

from app.social_publishing.domain.enums import PostStatus
from app.social_publishing.repositories.social_posts_repository import SocialPostsRepository
from app.social_publishing.services.publishing_orchestrator import PublishingOrchestrator
from app.services.logger import log

# How often to scan for due posts (seconds)
POLL_INTERVAL_SECONDS = 30

# Max posts to process per tick (prevents thundering herd)
BATCH_SIZE = 50


class SocialPublishingScheduler:
    """Background worker that transitions due posts into the publishing pipeline."""

    def __init__(
        self,
        posts_repo: SocialPostsRepository,
        orchestrator: PublishingOrchestrator,
        poll_interval: int = POLL_INTERVAL_SECONDS,
    ) -> None:
        self._posts = posts_repo
        self._orchestrator = orchestrator
        self._interval = poll_interval
        self._task: asyncio.Task | None = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the scheduler loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("SocialPublishingScheduler started", interval_s=self._interval)

    def stop(self) -> None:
        """Stop the scheduler loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        log.info("SocialPublishingScheduler stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._process_due_posts()
                await self._process_retryable_posts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("SocialPublishingScheduler loop error", error=str(e))
            await asyncio.sleep(self._interval)

    async def _process_due_posts(self) -> None:
        """Find SCHEDULED posts that are due and queue them."""
        now = datetime.now(timezone.utc)
        due_posts = await self._posts.find_due_for_scheduling(now, limit=BATCH_SIZE)

        for post in due_posts:
            # Atomically transition to QUEUED
            updated = await self._posts.update_status_by_id(post.id, PostStatus.QUEUED)
            if updated:
                log.info("Post queued for publishing", post_id=post.id, platform=post.platform.value)
                # Fire-and-forget publish task
                asyncio.create_task(self._safe_publish(post.id))

    async def _process_retryable_posts(self) -> None:
        """Find RETRYING posts and re-queue them."""
        now = datetime.now(timezone.utc)
        retryable = await self._posts.find_retryable(now, limit=BATCH_SIZE)

        for post in retryable:
            updated = await self._posts.update_status_by_id(post.id, PostStatus.QUEUED)
            if updated:
                log.info(
                    "Post re-queued for retry",
                    post_id=post.id,
                    attempt=post.retry_count + 1,
                )
                asyncio.create_task(self._safe_publish(post.id))

    async def _safe_publish(self, post_id: str) -> None:
        """Invoke the orchestrator, catching any unhandled exceptions."""
        try:
            await self._orchestrator.publish_post(post_id)
        except Exception as e:
            log.error("Orchestrator failed for post", post_id=post_id, error=str(e))
