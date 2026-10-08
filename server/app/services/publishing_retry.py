"""
Publishing Retry Worker — discovers failed posts eligible for retry and
re-fires them through the publishing trigger with exponential backoff.

Backoff schedule (from publishing_models):
  Retry 1: 30 seconds
  Retry 2: 2 minutes
  Retry 3: 8 minutes
  Retry 4: 30 minutes
  Retry 5: 2 hours

After max_retries attempts the post stays in 'failed' status permanently.

This worker follows the same asyncio background pattern as PollingScheduler:
  - start() / stop() lifecycle
  - Periodic loop with configurable interval
  - Never publishes directly — only fires the trigger

Integration:
  Started/stopped alongside the scheduler in scheduler_worker.py.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List

from app.database import db
from app.models.publishing_models import (
    DEFAULT_MAX_RETRIES,
    RETRY_BACKOFF_SECONDS,
)
from app.services.publishing.interfaces import IPublishTrigger
from app.services.logger import log

post_history_collection = db["post_history"]


class RetryWorker:
    """
    Periodically scans post_history for failed posts that are eligible for
    retry (retry_count < max_retries AND next_retry_at <= now). For each,
    marks the post as 'retrying', updates retry metadata, and fires the
    publishing trigger.
    """

    def __init__(self, trigger: IPublishTrigger, interval_seconds: int = 60):
        self._trigger = trigger
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("RetryWorker started", interval_s=self._interval)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        log.info("RetryWorker stopped")

    @property
    def running(self) -> bool:
        return self._running

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            try:
                retryable = await self._discover_retryable_posts()
                for post_id in retryable:
                    await self._trigger.fire(post_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("RetryWorker loop error", error=str(e))
            await asyncio.sleep(self._interval)

    # ── Discovery ─────────────────────────────────────────────────────────────

    async def _discover_retryable_posts(self) -> List[str]:
        """
        Find failed posts that:
          1. Have retry_count < max_retries (or max_retries not set → use default)
          2. Have next_retry_at <= now (their backoff delay has elapsed)

        For each, atomically update to 'retrying' status and bump retry_count
        so no other worker/iteration picks it up.
        """
        now = datetime.now(timezone.utc)
        retryable_ids: List[str] = []

        # Query: status=failed, next_retry_at exists and is in the past
        cursor = post_history_collection.find({
            "status": "failed",
            "next_retry_at": {"$ne": None, "$lte": now},
        }).limit(20)

        for post in await cursor.to_list(length=20):
            retry_count = post.get("retry_count", 0)
            max_retries = post.get("max_retries", DEFAULT_MAX_RETRIES)

            if retry_count >= max_retries:
                # Exhausted retries — clear next_retry_at so we don't scan it again
                await post_history_collection.update_one(
                    {"_id": post["_id"]},
                    {"$set": {"next_retry_at": None}},
                )
                continue

            # Atomically claim this post for retry
            new_retry_count = retry_count + 1
            next_backoff = _compute_next_retry_at(new_retry_count)

            result = await post_history_collection.update_one(
                {"_id": post["_id"], "status": "failed"},
                {"$set": {
                    "status": "retrying",
                    "retry_count": new_retry_count,
                    "next_retry_at": next_backoff,
                    "updated_at": now,
                }},
            )

            if result.modified_count > 0:
                post_id = str(post["_id"])
                retryable_ids.append(post_id)
                log.info(
                    f"Retrying post {post.get('unique_post_id', post_id)}",
                    platform=post.get("platform", ""),
                    attempt=new_retry_count,
                    max_retries=max_retries,
                )

        return retryable_ids


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_next_retry_at(retry_count: int) -> datetime:
    """
    Compute the next_retry_at timestamp based on the current retry_count.
    Used both for scheduling the NEXT retry (if this one also fails) and
    as the initial backoff when a post first fails.
    """
    index = min(retry_count, len(RETRY_BACKOFF_SECONDS)) - 1
    if index < 0:
        index = 0
    delay_seconds = RETRY_BACKOFF_SECONDS[index]
    return datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)


def schedule_retry_for_failed_post(retry_count: int = 0) -> dict:
    """
    Compute the retry fields to set when a post transitions to 'failed'.
    Called by the publishing service when a publish attempt fails.

    Returns a dict of fields to $set on the post_history document:
        retry_count, max_retries, next_retry_at, last_retry_reason

    If retry_count >= DEFAULT_MAX_RETRIES, next_retry_at is None (no more retries).
    """
    if retry_count >= DEFAULT_MAX_RETRIES:
        return {
            "retry_count": retry_count,
            "max_retries": DEFAULT_MAX_RETRIES,
            "next_retry_at": None,
        }

    return {
        "retry_count": retry_count,
        "max_retries": DEFAULT_MAX_RETRIES,
        "next_retry_at": _compute_next_retry_at(retry_count + 1),
    }
