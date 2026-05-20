"""
Background scheduler worker - processes scheduled posts when their time arrives.
Uses asyncio tasks to periodically check for due posts and publish them.
"""

import asyncio
from datetime import datetime, timezone
from app.database import db
from app.services.platform_adapters import get_adapter
from app.models.publishing_models import PLATFORM_MODES

# Reference to the background task
_scheduler_task = None
_running = False

# Collections
post_history_collection = db["post_history"]
scheduled_posts_collection = db["scheduled_posts"]


async def process_due_posts():
    """
    Find and process all posts that are due for publishing.
    Handles timezone-aware comparison: the stored scheduled_at is in the user's
    local timezone (naive datetime). We convert it to UTC using the stored timezone
    before comparing with the current UTC time.
    """
    from zoneinfo import ZoneInfo

    now_utc = datetime.now(timezone.utc)

    # Fetch ALL scheduled posts (don't filter by time in query — do it in Python)
    query = {"status": "scheduled"}
    cursor = post_history_collection.find(query).limit(50)
    scheduled_posts = await cursor.to_list(length=50)

    for post in scheduled_posts:
        scheduled_at = post.get("scheduled_at")
        if not scheduled_at:
            continue

        post_tz_str = post.get("timezone", "Asia/Kolkata")

        # Convert the stored local time to UTC for comparison
        try:
            if scheduled_at.tzinfo is None:
                # Naive datetime — interpret as user's local timezone
                local_tz = ZoneInfo(post_tz_str)
                scheduled_utc = scheduled_at.replace(tzinfo=local_tz).astimezone(timezone.utc)
            else:
                scheduled_utc = scheduled_at.astimezone(timezone.utc)
        except Exception:
            # Fallback: treat as UTC
            scheduled_utc = scheduled_at.replace(tzinfo=timezone.utc)

        # Only process if the scheduled time has passed
        if now_utc >= scheduled_utc:
            await process_single_post(post)

    # Also check the legacy scheduled_posts collection
    legacy_query = {"status": "scheduled"}
    legacy_cursor = scheduled_posts_collection.find(legacy_query).limit(20)
    legacy_posts = await legacy_cursor.to_list(length=20)

    for post in legacy_posts:
        scheduled_at = post.get("scheduled_at")
        if scheduled_at and scheduled_at.replace(tzinfo=timezone.utc) <= now_utc:
            await process_legacy_scheduled_post(post)


async def process_single_post(post: dict):
    """Process a single due post - attempt publishing."""
    post_id = post["_id"]
    platform = post.get("platform", "")
    content = post.get("content", "")
    user_id = post.get("user_id", "")
    media_urls = post.get("media_urls", [])

    # Mark as posting
    await post_history_collection.update_one(
        {"_id": post_id},
        {"$set": {"status": "posting", "updated_at": datetime.now(timezone.utc)}},
    )

    mode = PLATFORM_MODES.get(platform, "manual_assisted")

    if mode == "manual_assisted":
        # Move to awaiting_manual_publish
        await post_history_collection.update_one(
            {"_id": post_id},
            {"$set": {
                "status": "awaiting_manual_publish",
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return

    # Attempt auto-publishing
    try:
        adapter = get_adapter(platform)
        result = await adapter.publish_post(user_id, content, media_urls)

        if result.get("success"):
            await post_history_collection.update_one(
                {"_id": post_id},
                {"$set": {
                    "status": "posted",
                    "posted_at": datetime.now(timezone.utc),
                    "platform_post_id": result.get("platform_post_id"),
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            # Emit real-time event + create notification
            from app.services.event_bus import emit_post_published
            from app.services.notification_service import create_notification, send_post_published_email
            post_data = {"platform": platform, "unique_post_id": post.get("unique_post_id", ""), "content_preview": content[:80]}
            await emit_post_published(user_id, post_data)
            await create_notification(
                user_id,
                f"{platform.title()} post published!",
                f"Your scheduled post {post.get('unique_post_id', '')} has been published successfully.",
                platform=platform,
                post_id=str(post_id),
            )
            # Send email notification (get user email)
            from app.models.user_model import users_collection
            from bson import ObjectId as ObjId
            user_doc = await users_collection.find_one({"_id": ObjId(user_id)})
            if user_doc and user_doc.get("email"):
                await send_post_published_email(user_doc["email"], platform, post.get("unique_post_id", ""), content[:200])
        else:
            failure_reason = result.get("error", "Unknown error")
            await post_history_collection.update_one(
                {"_id": post_id},
                {"$set": {
                    "status": "failed",
                    "failure_reason": failure_reason,
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            # Emit failure event + notification
            from app.services.event_bus import emit_post_failed
            from app.services.notification_service import create_notification, send_post_failed_email
            post_data = {"platform": platform, "unique_post_id": post.get("unique_post_id", ""), "error": failure_reason}
            await emit_post_failed(user_id, post_data)
            await create_notification(
                user_id,
                f"{platform.title()} post failed",
                f"Post {post.get('unique_post_id', '')} failed: {failure_reason}",
                platform=platform,
                post_id=str(post_id),
            )
            from app.models.user_model import users_collection
            from bson import ObjectId as ObjId
            user_doc = await users_collection.find_one({"_id": ObjId(user_id)})
            if user_doc and user_doc.get("email"):
                await send_post_failed_email(user_doc["email"], platform, post.get("unique_post_id", ""), failure_reason)
    except Exception as e:
        await post_history_collection.update_one(
            {"_id": post_id},
            {"$set": {
                "status": "failed",
                "failure_reason": str(e),
                "updated_at": datetime.now(timezone.utc),
            }},
        )


async def process_legacy_scheduled_post(post: dict):
    """Process a legacy scheduled post from the old collection."""
    post_id = post["_id"]
    platforms = post.get("selected_platforms", [])
    platform_versions = post.get("platform_versions", {})
    original_content = post.get("original_content", "")
    user_id = post.get("user_id", "")

    # Mark as publishing in legacy collection
    await scheduled_posts_collection.update_one(
        {"_id": post_id},
        {"$set": {"status": "publishing", "updated_at": datetime.now(timezone.utc)}},
    )

    all_success = True
    for platform in platforms:
        content = platform_versions.get(platform, original_content)
        mode = PLATFORM_MODES.get(platform, "manual_assisted")

        if mode == "manual_assisted":
            continue

        try:
            adapter = get_adapter(platform)
            result = await adapter.publish_post(user_id, content)
            if not result.get("success"):
                all_success = False
        except Exception:
            all_success = False

    # Update legacy post status
    final_status = "published" if all_success else "failed"
    await scheduled_posts_collection.update_one(
        {"_id": post_id},
        {"$set": {"status": final_status, "updated_at": datetime.now(timezone.utc)}},
    )


async def cleanup_stuck_posts():
    """Mark posts stuck in 'posting' for more than 5 minutes as failed."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    result = await post_history_collection.update_many(
        {"status": "posting", "updated_at": {"$lt": cutoff}},
        {"$set": {"status": "failed", "failure_reason": "Publishing timed out", "updated_at": datetime.now(timezone.utc)}},
    )
    if result.modified_count > 0:
        print(f"[Scheduler] Cleaned up {result.modified_count} stuck posts")


async def scheduler_loop():
    """Main scheduler loop - runs every 30 seconds."""
    global _running
    while _running:
        try:
            await process_due_posts()
            await cleanup_stuck_posts()
        except Exception as e:
            print(f"[Scheduler] Error processing due posts: {e}")
        await asyncio.sleep(30)


def start_scheduler():
    """Start the background scheduler."""
    global _scheduler_task, _running
    _running = True
    loop = asyncio.get_event_loop()
    _scheduler_task = loop.create_task(scheduler_loop())


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler_task, _running
    _running = False
    if _scheduler_task:
        _scheduler_task.cancel()
        _scheduler_task = None
