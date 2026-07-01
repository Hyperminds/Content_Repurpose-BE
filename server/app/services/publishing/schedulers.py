"""
Schedulers — decide WHEN posts publish, then dispatch via a trigger.

A scheduler NEVER publishes. It discovers due posts and calls `trigger.fire(id)`.
That is the whole separation: swap the scheduler (polling ↔ cloud) or the trigger
(in-process ↔ SQS) independently, and the publishing service never changes.

  • PollingScheduler        — periodic MongoDB scan for due posts (current behaviour,
                              minus the publishing logic, which now lives in the
                              publishing service).
  • AWSEventDrivenScheduler — PREPARED STUB. With AWS, EventBridge Scheduler fires
                              on each post's scheduled_at and there is no polling
                              loop at all.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import List
from zoneinfo import ZoneInfo

from app.database import db
from app.services.publishing.interfaces import IScheduler, IPublishTrigger

post_history_collection = db["post_history"]
scheduled_posts_collection = db["scheduled_posts"]  # legacy


class PollingScheduler(IScheduler):
    """
    Periodically scans `post_history` for due scheduled posts and fires the
    trigger for each. Also performs a maintenance sweep for stuck posts.
    """

    def __init__(self, trigger: IPublishTrigger, interval_seconds: int = 30):
        self._trigger = trigger
        self._interval = interval_seconds
        self._task = None
        self._running = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    @property
    def running(self) -> bool:
        return self._running

    async def _loop(self):
        while self._running:
            try:
                due = await self.discover_due_posts()
                for post_id in due:
                    await self._trigger.fire(post_id)      # dispatch only — no publishing
                await self._cleanup_stuck_posts()
                await self._process_legacy()               # deprecated path, preserved
            except Exception as e:
                print(f"[scheduler:polling] loop error: {e}")
            await asyncio.sleep(self._interval)

    # ── discovery (the "WHEN") ───────────────────────────────────────────────

    async def discover_due_posts(self) -> List[str]:
        """
        Return ids of `post_history` posts whose scheduled time has passed.
        Timezone handling preserved from the original implementation: stored
        scheduled_at may be naive (in the post's local tz) or tz-aware.
        """
        now_utc = datetime.now(timezone.utc)
        due_ids: List[str] = []

        cursor = post_history_collection.find({"status": "scheduled"}).limit(50)
        for post in await cursor.to_list(length=50):
            scheduled_at = post.get("scheduled_at")
            if not scheduled_at:
                continue
            tz_str = post.get("timezone", "Asia/Kolkata")
            try:
                if scheduled_at.tzinfo is None:
                    scheduled_utc = scheduled_at.replace(tzinfo=ZoneInfo(tz_str)).astimezone(timezone.utc)
                else:
                    scheduled_utc = scheduled_at.astimezone(timezone.utc)
            except Exception:
                scheduled_utc = scheduled_at.replace(tzinfo=timezone.utc)

            if now_utc >= scheduled_utc:
                due_ids.append(str(post["_id"]))

        return due_ids

    # ── maintenance ──────────────────────────────────────────────────────────

    async def _cleanup_stuck_posts(self):
        """Posts stuck in 'posting' for >5 min are marked failed."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = await post_history_collection.update_many(
            {"status": "posting", "updated_at": {"$lt": cutoff}},
            {"$set": {"status": "failed", "failure_reason": "Publishing timed out",
                      "updated_at": datetime.now(timezone.utc)}},
        )
        if result.modified_count:
            print(f"[scheduler:polling] cleaned up {result.modified_count} stuck posts")

    # ── legacy (deprecated) ──────────────────────────────────────────────────

    async def _process_legacy(self):
        """
        DEPRECATED: legacy `scheduled_posts` collection (pre-post_history).
        Preserved so existing legacy rows still publish. New code should not
        write to this collection. Slated for migration onto post_history.
        """
        from app.models.publishing_models import PLATFORM_MODES
        from app.services.platform_adapters import get_adapter

        now_utc = datetime.now(timezone.utc)
        cursor = scheduled_posts_collection.find({"status": "scheduled"}).limit(20)
        for post in await cursor.to_list(length=20):
            scheduled_at = post.get("scheduled_at")
            if not (scheduled_at and scheduled_at.replace(tzinfo=timezone.utc) <= now_utc):
                continue

            await scheduled_posts_collection.update_one(
                {"_id": post["_id"]},
                {"$set": {"status": "publishing", "updated_at": now_utc}},
            )
            all_success = True
            for platform in post.get("selected_platforms", []):
                if PLATFORM_MODES.get(platform, "manual_assisted") == "manual_assisted":
                    continue
                content = post.get("platform_versions", {}).get(platform, post.get("original_content", ""))
                try:
                    adapter = get_adapter(platform)
                    res = await adapter.publish_post(post.get("user_id", ""), content)
                    if not res.get("success"):
                        all_success = False
                except Exception:
                    all_success = False
            await scheduled_posts_collection.update_one(
                {"_id": post["_id"]},
                {"$set": {"status": "published" if all_success else "failed",
                          "updated_at": datetime.now(timezone.utc)}},
            )


class AWSEventDrivenScheduler(IScheduler):
    """
    PREPARED STUB — not implemented.

    With AWS there is no polling. When a post is scheduled, you create a
    one-shot Amazon EventBridge Scheduler entry for its `scheduled_at`. When it
    fires, the target trigger calls `publish_post(post_id)`. `discover_due_posts`
    is unused in that model.

    Integration points (no boto3 here):
        • on schedule: scheduler.create_schedule(... ScheduleExpression=at(ts) ...)
        • on cancel:   scheduler.delete_schedule(name)
    """

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "AWSEventDrivenScheduler is a prepared stub. With EventBridge "
            "Scheduler there is no polling loop — create a one-shot schedule per "
            "post that targets the publish trigger."
        )

    def start(self) -> None:  # pragma: no cover - stub
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - stub
        raise NotImplementedError

    async def discover_due_posts(self) -> List[str]:  # pragma: no cover - stub
        raise NotImplementedError
