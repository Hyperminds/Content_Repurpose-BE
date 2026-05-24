"""
Publishing service - handles instant posting, scheduling, and manual-assisted publishing.
Generates unique post IDs, manages post history, and coordinates platform adapters.
"""

import asyncio
from datetime import datetime, timezone
from bson import ObjectId
from app.database import db
from app.models.publishing_models import (
    VALID_POST_STATUSES,
    PLATFORM_MODES,
    PLATFORM_PREFIXES,
)

# Collections
post_history_collection = db["post_history"]
platform_catalog_collection = db["platform_catalog"]
connected_platforms_collection = db["connected_platforms"]


# ============ UNIQUE POST ID GENERATION ============ #

async def generate_unique_post_id(platform: str) -> str:
    """Generate a guaranteed unique post ID like REP-LINK-A3F2."""
    import random, string
    prefix = PLATFORM_PREFIXES.get(platform, "UNK")
    # Use timestamp millis + random suffix — collision-proof
    ts = int(datetime.now(timezone.utc).timestamp() * 1000) % 100000
    rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    candidate = f"REP-{prefix}-{ts:05d}-{rand}"
    # Ensure uniqueness in DB (extremely unlikely to collide but safe)
    while await post_history_collection.count_documents({"unique_post_id": candidate}) > 0:
        rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        candidate = f"REP-{prefix}-{ts:05d}-{rand}"
    return candidate


# ============ POST HISTORY CRUD ============ #

def serialize_post_history(doc):
    """Convert MongoDB document to JSON-serializable dict."""
    return {
        "id": str(doc["_id"]),
        "unique_post_id": doc.get("unique_post_id"),
        "tracking_id": doc.get("tracking_id"),
        "user_id": doc.get("user_id"),
        "platform": doc.get("platform"),
        "content": doc.get("content"),
        "content_preview": doc.get("content_preview"),
        "media_urls": doc.get("media_urls", []),
        "publish_type": doc.get("publish_type"),
        "status": doc.get("status"),
        "scheduled_at": doc.get("scheduled_at").isoformat() if doc.get("scheduled_at") else None,
        "posted_at": doc.get("posted_at").isoformat() if doc.get("posted_at") else None,
        "manually_published_at": doc.get("manually_published_at").isoformat() if doc.get("manually_published_at") else None,
        "failure_reason": doc.get("failure_reason"),
        "platform_post_id": doc.get("platform_post_id"),
        "analytics_snapshot": doc.get("analytics_snapshot"),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
        "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
    }


async def create_post_history(
    user_id: str,
    platform: str,
    content: str,
    publish_type: str,
    status: str = "pending",
    scheduled_at: datetime = None,
    media_urls: list = None,
    tracking_id: str = None,
):
    """Create a new post history entry with a unique post ID."""
    unique_post_id = await generate_unique_post_id(platform)
    now = datetime.now(timezone.utc)

    doc = {
        "unique_post_id": unique_post_id,
        "tracking_id": tracking_id or unique_post_id,
        "user_id": user_id,
        "platform": platform,
        "content": content,
        "content_preview": content[:100] if content else "",
        "media_urls": media_urls or [],
        "publish_type": publish_type,
        "status": status,
        "scheduled_at": scheduled_at,
        "posted_at": None,
        "manually_published_at": None,
        "failure_reason": None,
        "platform_post_id": None,
        "analytics_snapshot": None,
        "created_at": now,
        "updated_at": now,
    }

    result = await post_history_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_post_history(doc)


async def get_post_history(
    user_id: str,
    platform: str = None,
    status: str = None,
    publish_type: str = None,
    limit: int = 50,
    offset: int = 0,
):
    """Get post history with optional filters."""
    query = {"user_id": user_id}
    if platform:
        query["platform"] = platform
    if status:
        query["status"] = status
    if publish_type:
        query["publish_type"] = publish_type

    cursor = (
        post_history_collection.find(query)
        .sort("created_at", -1)
        .skip(offset)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    total = await post_history_collection.count_documents(query)
    return {"posts": [serialize_post_history(doc) for doc in docs], "total": total}


async def get_post_by_id(post_id: str, user_id: str):
    """Get a single post history entry."""
    doc = await post_history_collection.find_one({
        "_id": ObjectId(post_id),
        "user_id": user_id,
    })
    if not doc:
        return None
    return serialize_post_history(doc)


async def update_post_status(post_id: str, user_id: str, status: str, **kwargs):
    """Update post status and optional fields."""
    if status not in VALID_POST_STATUSES:
        return {"error": f"Invalid status: {status}"}

    update_fields = {"status": status, "updated_at": datetime.now(timezone.utc)}

    if status == "posted":
        update_fields["posted_at"] = datetime.now(timezone.utc)
    elif status == "manually_published":
        update_fields["manually_published_at"] = datetime.now(timezone.utc)
    elif status == "failed":
        update_fields["failure_reason"] = kwargs.get("failure_reason", "Unknown error")

    if "platform_post_id" in kwargs:
        update_fields["platform_post_id"] = kwargs["platform_post_id"]

    result = await post_history_collection.update_one(
        {"_id": ObjectId(post_id), "user_id": user_id},
        {"$set": update_fields},
    )

    if result.modified_count == 0:
        return {"error": "Post not found or not owned by user"}

    doc = await post_history_collection.find_one({"_id": ObjectId(post_id)})
    return serialize_post_history(doc)


async def delete_post_history_entry(post_id: str, user_id: str):
    """Delete a post history entry."""
    result = await post_history_collection.delete_one({
        "_id": ObjectId(post_id),
        "user_id": user_id,
    })
    if result.deleted_count == 0:
        return {"error": "Post not found"}
    return {"message": "Post history entry deleted"}


# ============ INSTANT PUBLISHING ============ #

async def publish_instantly(user_id: str, platform: str, content: str, media_urls: list = None):
    """
    Publish content instantly to a platform.
    For auto platforms: attempts API publishing.
    For manual-assisted: creates a ready_to_publish entry.
    """
    mode = PLATFORM_MODES.get(platform, "manual_assisted")

    if mode == "manual_assisted":
        # Create entry with ready_to_publish status
        post = await create_post_history(
            user_id=user_id,
            platform=platform,
            content=content,
            publish_type="instant",
            status="ready_to_publish",
            media_urls=media_urls,
        )
        return {
            "post": post,
            "action": "manual",
            "image_url": media_urls[0] if media_urls else None,
            "message": f"Content ready for manual publishing on {platform}. Copy and paste to the platform.",
        }
    else:
        # Auto publishing - attempt via platform adapter
        post = await create_post_history(
            user_id=user_id,
            platform=platform,
            content=content,
            publish_type="instant",
            status="posting",
            media_urls=media_urls,
        )

        # Try to publish via adapter
        try:
            from app.services.platform_adapters import get_adapter
            adapter = get_adapter(platform)
            result = await adapter.publish_post(user_id, content, media_urls)

            if result.get("success"):
                updated = await update_post_status(
                    post["id"], user_id, "posted",
                    platform_post_id=result.get("platform_post_id"),
                )
                return {
                    "post": updated,
                    "action": "published",
                    "message": f"Successfully published to {platform}!",
                }
            elif result.get("needs_connection"):
                # Platform not connected - switch to manual flow
                updated = await update_post_status(
                    post["id"], user_id, "ready_to_publish",
                )
                return {
                    "post": updated,
                    "action": "manual",
                    "needs_connection": True,
                    "image_url": media_urls[0] if media_urls else None,
                    "message": f"{platform.title()} not connected. Content copied — paste it manually, or connect your account in Platforms.",
                }
            else:
                updated = await update_post_status(
                    post["id"], user_id, "failed",
                    failure_reason=result.get("error", "Publishing failed"),
                )
                return {
                    "post": updated,
                    "action": "failed",
                    "message": result.get("error", "Publishing failed"),
                }
        except Exception as e:
            updated = await update_post_status(
                post["id"], user_id, "failed",
                failure_reason=str(e),
            )
            return {
                "post": updated,
                "action": "failed",
                "message": f"Publishing failed: {str(e)}",
            }


# ============ SCHEDULE PUBLISHING ============ #

async def schedule_post(
    user_id: str,
    platform: str,
    content: str,
    scheduled_at: datetime,
    timezone_str: str = "UTC",
    media_urls: list = None,
):
    """Schedule a post for future publishing."""
    post = await create_post_history(
        user_id=user_id,
        platform=platform,
        content=content,
        publish_type="scheduled",
        status="scheduled",
        scheduled_at=scheduled_at,
        media_urls=media_urls,
        tracking_id=None,
    )

    # Store timezone separately for the scheduler
    from app.database import db
    post_history_col = db["post_history"]
    from bson import ObjectId
    await post_history_col.update_one(
        {"_id": ObjectId(post["id"])},
        {"$set": {"timezone": timezone_str}},
    )

    # Emit real-time event
    from app.services.event_bus import emit_post_scheduled
    await emit_post_scheduled(user_id, post)

    return {
        "post": post,
        "message": f"Post scheduled for {platform} at {scheduled_at.isoformat()}",
    }


# ============ MANUAL PUBLISHING ============ #

async def mark_manually_published(post_id: str, user_id: str):
    """Mark a manual-assisted post as manually published."""
    return await update_post_status(post_id, user_id, "manually_published")


# ============ RETRY FAILED POST ============ #

async def retry_failed_post(post_id: str, user_id: str):
    """Retry a failed post."""
    doc = await post_history_collection.find_one({
        "_id": ObjectId(post_id),
        "user_id": user_id,
        "status": "failed",
    })
    if not doc:
        return {"error": "Failed post not found"}

    # Re-attempt publishing
    return await publish_instantly(
        user_id=user_id,
        platform=doc["platform"],
        content=doc["content"],
        media_urls=doc.get("media_urls"),
    )


# ============ PLATFORM CATALOG ============ #

async def get_platform_catalog():
    """Get all platforms in the catalog."""
    docs = await platform_catalog_collection.find().to_list(length=20)
    if not docs:
        # Initialize with defaults if empty
        await init_platform_catalog()
        docs = await platform_catalog_collection.find().to_list(length=20)
    return [serialize_platform_catalog(doc) for doc in docs]


def serialize_platform_catalog(doc):
    return {
        "id": str(doc["_id"]),
        "platform_name": doc.get("platform_name"),
        "display_name": doc.get("display_name"),
        "icon": doc.get("icon"),
        "enabled": doc.get("enabled", True),
        "posting_mode": doc.get("posting_mode"),
        "api_status": doc.get("api_status", "not_configured"),
        "oauth_supported": doc.get("oauth_supported", False),
        "supported_media_types": doc.get("supported_media_types", []),
        "character_limit": doc.get("character_limit"),
        "hashtag_limit": doc.get("hashtag_limit"),
        "posting_limits": doc.get("posting_limits", {}),
        "platform_rules": doc.get("platform_rules", ""),
    }


async def init_platform_catalog():
    """Initialize platform catalog with default entries."""
    platforms = [
        {
            "platform_name": "linkedin",
            "display_name": "LinkedIn",
            "icon": "💼",
            "enabled": True,
            "posting_mode": "auto",
            "api_status": "not_configured",
            "oauth_supported": True,
            "supported_media_types": ["text", "image", "article", "video"],
            "character_limit": 3000,
            "hashtag_limit": 30,
            "posting_limits": {"daily": 20},
            "platform_rules": "Professional tone. No excessive hashtags. Article-style posts perform best.",
        },
        {
            "platform_name": "instagram",
            "display_name": "Instagram",
            "icon": "📸",
            "enabled": True,
            "posting_mode": "auto",
            "api_status": "not_configured",
            "oauth_supported": True,
            "supported_media_types": ["image", "carousel", "video", "text"],
            "character_limit": 2200,
            "hashtag_limit": 30,
            "posting_limits": {"daily": 10},
            "platform_rules": "Visual-first platform. Hashtags drive discovery. Reels get priority.",
        },
        {
            "platform_name": "twitter",
            "display_name": "Twitter / X",
            "icon": "🐦",
            "enabled": True,
            "posting_mode": "manual_assisted",
            "api_status": "not_configured",
            "oauth_supported": True,
            "supported_media_types": ["text", "image", "video"],
            "character_limit": 280,
            "hashtag_limit": 5,
            "posting_limits": {"daily": 50},
            "platform_rules": "Short, punchy content. Threads for longer content. Engagement-driven.",
        },
        {
            "platform_name": "reddit",
            "display_name": "Reddit",
            "icon": "🤖",
            "enabled": True,
            "posting_mode": "auto",
            "api_status": "not_configured",
            "oauth_supported": True,
            "supported_media_types": ["text", "image", "video"],
            "character_limit": 40000,
            "hashtag_limit": 0,
            "posting_limits": {"daily": 5},
            "platform_rules": "No hashtags. Authentic tone. Value-driven. Subreddit rules vary.",
        },
        {
            "platform_name": "medium",
            "display_name": "Medium",
            "icon": "✍️",
            "enabled": True,
            "posting_mode": "auto",
            "api_status": "not_configured",
            "oauth_supported": True,
            "supported_media_types": ["text", "article", "image"],
            "character_limit": None,
            "hashtag_limit": 5,
            "posting_limits": {"daily": 3},
            "platform_rules": "Long-form content. SEO-friendly. Tags for discoverability.",
        },
        {
            "platform_name": "meta",
            "display_name": "Meta / Facebook",
            "icon": "👥",
            "enabled": True,
            "posting_mode": "auto",
            "api_status": "not_configured",
            "oauth_supported": True,
            "supported_media_types": ["text", "image", "video", "carousel"],
            "character_limit": 63206,
            "hashtag_limit": 10,
            "posting_limits": {"daily": 10},
            "platform_rules": "Community-driven. Questions drive engagement. Avoid link-heavy posts.",
        },
        {
            "platform_name": "quora",
            "display_name": "Quora",
            "icon": "❓",
            "enabled": True,
            "posting_mode": "manual_assisted",
            "api_status": "not_configured",
            "oauth_supported": False,
            "supported_media_types": ["text", "image"],
            "character_limit": None,
            "hashtag_limit": 0,
            "posting_limits": {"daily": 10},
            "platform_rules": "Answer format. No hashtags. Authority-driven. SEO-friendly.",
        },
    ]

    await platform_catalog_collection.insert_many(platforms)


async def update_platform_catalog(platform_name: str, updates: dict):
    """Update a platform catalog entry (admin only)."""
    allowed_fields = ["enabled", "posting_mode", "api_status", "posting_limits", "platform_rules"]
    update_fields = {k: v for k, v in updates.items() if k in allowed_fields}

    if not update_fields:
        return {"error": "No valid fields to update"}

    result = await platform_catalog_collection.update_one(
        {"platform_name": platform_name},
        {"$set": update_fields},
    )
    if result.modified_count == 0:
        return {"error": "Platform not found"}

    doc = await platform_catalog_collection.find_one({"platform_name": platform_name})
    return serialize_platform_catalog(doc)


# ============ ANALYTICS / DASHBOARD ============ #

async def get_publishing_stats(user_id: str):
    """Get publishing statistics for dashboard."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {
            "_id": "$status",
            "count": {"$sum": 1},
        }},
    ]
    results = await post_history_collection.aggregate(pipeline).to_list(length=20)
    stats = {r["_id"]: r["count"] for r in results}

    # Platform breakdown
    platform_pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {
            "_id": "$platform",
            "count": {"$sum": 1},
            "posted": {"$sum": {"$cond": [{"$eq": ["$status", "posted"]}, 1, 0]}},
            "failed": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
        }},
    ]
    platform_results = await post_history_collection.aggregate(platform_pipeline).to_list(length=20)

    total = sum(stats.values())
    posted = stats.get("posted", 0) + stats.get("manually_published", 0)
    success_rate = round((posted / total * 100), 1) if total > 0 else 0

    return {
        "total_posts": total,
        "posted": posted,
        "failed": stats.get("failed", 0),
        "scheduled": stats.get("scheduled", 0),
        "pending_manual": stats.get("ready_to_publish", 0) + stats.get("awaiting_manual_publish", 0),
        "success_rate": success_rate,
        "by_status": stats,
        "by_platform": {r["_id"]: {"total": r["count"], "posted": r["posted"], "failed": r["failed"]} for r in platform_results},
    }
