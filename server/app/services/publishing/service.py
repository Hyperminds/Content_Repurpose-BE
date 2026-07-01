"""
PostPublishingService — the single, caller-agnostic publishing entry point.

This contains the publishing logic that previously lived inline in
`scheduler_worker.process_single_post`. It knows HOW to publish a post and what
side effects to emit, but nothing about WHEN or WHO triggered it.
"""

from datetime import datetime, timezone
from bson import ObjectId

from app.database import db
from app.models.publishing_models import PLATFORM_MODES
from app.services.platform_adapters import get_adapter
from app.services.publishing.interfaces import (
    IPublishingService,
    PublishOutcome,
    PublishResult,
)

post_history_collection = db["post_history"]

# Statuses that are already final — never re-publish these.
_TERMINAL_STATUSES = {"posted", "manually_published", "cancelled"}


class PostPublishingService(IPublishingService):
    """Publishes a single `post_history` document by id."""

    async def publish_post(self, post_id: str) -> PublishResult:
        post = await self._load(post_id)
        if not post:
            return PublishResult(post_id=str(post_id), outcome=PublishOutcome.SKIPPED,
                                 error="Post not found")

        status = post.get("status", "")
        if status in _TERMINAL_STATUSES:
            return PublishResult(
                post_id=str(post_id), outcome=PublishOutcome.SKIPPED,
                platform=post.get("platform"), error=f"Already {status}",
            )

        platform = post.get("platform", "")
        content = post.get("content", "")
        user_id = post.get("user_id", "")
        media_urls = post.get("media_urls", [])
        oid = post["_id"]

        # Mark as in-flight.
        await self._set_status(oid, "posting")

        # Manual-assisted platforms are never auto-posted — they wait for the user.
        if PLATFORM_MODES.get(platform, "manual_assisted") == "manual_assisted":
            await self._set_status(oid, "awaiting_manual_publish")
            return PublishResult(post_id=str(oid), outcome=PublishOutcome.AWAITING_MANUAL,
                                 platform=platform)

        # Auto-publish via the platform adapter.
        try:
            adapter = get_adapter(platform)
            result = await adapter.publish_post(user_id, content, media_urls)
        except Exception as e:
            await self._mark_failed(oid, str(e))
            return PublishResult(post_id=str(oid), outcome=PublishOutcome.FAILED,
                                 platform=platform, error=str(e))

        if result.get("success"):
            platform_post_id = result.get("platform_post_id")
            await self._mark_posted(oid, platform_post_id)
            await self._on_published(user_id, platform, post, content)
            return PublishResult(
                post_id=str(oid), outcome=PublishOutcome.PUBLISHED,
                platform=platform, platform_post_id=platform_post_id,
            )

        failure_reason = result.get("error", "Unknown error")
        await self._mark_failed(oid, failure_reason)
        await self._on_failed(user_id, platform, post, failure_reason)
        return PublishResult(post_id=str(oid), outcome=PublishOutcome.FAILED,
                             platform=platform, error=failure_reason)

    # ── persistence helpers ──────────────────────────────────────────────────

    async def _load(self, post_id) -> dict:
        try:
            oid = post_id if isinstance(post_id, ObjectId) else ObjectId(str(post_id))
        except Exception:
            return None
        return await post_history_collection.find_one({"_id": oid})

    async def _set_status(self, oid, status: str):
        await post_history_collection.update_one(
            {"_id": oid},
            {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}},
        )

    async def _mark_posted(self, oid, platform_post_id):
        await post_history_collection.update_one(
            {"_id": oid},
            {"$set": {
                "status": "posted",
                "posted_at": datetime.now(timezone.utc),
                "platform_post_id": platform_post_id,
                "updated_at": datetime.now(timezone.utc),
            }},
        )

    async def _mark_failed(self, oid, reason: str):
        await post_history_collection.update_one(
            {"_id": oid},
            {"$set": {
                "status": "failed",
                "failure_reason": reason,
                "updated_at": datetime.now(timezone.utc),
            }},
        )

    # ── side effects (events / notifications / email) ────────────────────────

    async def _on_published(self, user_id, platform, post, content):
        try:
            from app.services.event_bus import emit_post_published
            from app.services.notification_service import create_notification, send_post_published_email
            unique_id = post.get("unique_post_id", "")
            await emit_post_published(user_id, {
                "platform": platform, "unique_post_id": unique_id, "content_preview": content[:80],
            })
            await create_notification(
                user_id, f"{platform.title()} post published!",
                f"Your scheduled post {unique_id} has been published successfully.",
                platform=platform, post_id=str(post.get("_id")),
            )
            await self._email(user_id, send_post_published_email, platform, unique_id, content[:200])
        except Exception as e:
            print(f"[publishing] post-publish side effects failed: {e}")

    async def _on_failed(self, user_id, platform, post, failure_reason):
        try:
            from app.services.event_bus import emit_post_failed
            from app.services.notification_service import create_notification, send_post_failed_email
            unique_id = post.get("unique_post_id", "")
            await emit_post_failed(user_id, {
                "platform": platform, "unique_post_id": unique_id, "error": failure_reason,
            })
            await create_notification(
                user_id, f"{platform.title()} post failed",
                f"Post {unique_id} failed: {failure_reason}",
                platform=platform, post_id=str(post.get("_id")),
            )
            await self._email(user_id, send_post_failed_email, platform, unique_id, failure_reason)
        except Exception as e:
            print(f"[publishing] post-fail side effects failed: {e}")

    async def _email(self, user_id, email_fn, platform, unique_id, body):
        try:
            from app.models.user_model import users_collection
            user_doc = await users_collection.find_one({"_id": ObjectId(user_id)})
            if user_doc and user_doc.get("email"):
                await email_fn(user_doc["email"], platform, unique_id, body)
        except Exception:
            pass
