"""
Publishing controller - handles request validation and delegates to publishing service.
"""

from datetime import datetime
from app.services.publishing_service import (
    publish_instantly,
    schedule_post,
    mark_manually_published,
    retry_failed_post,
    get_post_history,
    get_post_by_id,
    update_post_status,
    delete_post_history_entry,
    get_platform_catalog,
    update_platform_catalog,
    get_publishing_stats,
)
from app.services.platform_adapters import get_adapter
from app.models.publishing_models import VALID_POST_STATUSES


# ============ PUBLISH NOW ============ #

async def handle_publish_now(data: dict, user_id: str):
    """Handle instant publishing request."""
    platform = data.get("platform", "").strip().lower()
    content = data.get("content", "").strip()
    media_urls = data.get("media_urls", [])

    if not platform:
        return {"error": "Platform is required"}
    if not content:
        return {"error": "Content is required"}

    # Validate content against platform rules
    try:
        adapter = get_adapter(platform)
        validation = adapter.validate_content(content)
        if not validation["valid"]:
            return {"error": f"Content validation failed: {'; '.join(validation['errors'])}"}
    except ValueError as e:
        return {"error": str(e)}

    return await publish_instantly(user_id, platform, content, media_urls)


# ============ SCHEDULE POST ============ #

async def handle_schedule_post(data: dict, user_id: str):
    """Handle scheduling a post for future publishing."""
    platform = data.get("platform", "").strip().lower()
    content = data.get("content", "").strip()
    scheduled_at_str = data.get("scheduled_at", "")
    timezone_str = data.get("timezone", "UTC")
    media_urls = data.get("media_urls", [])

    if not platform:
        return {"error": "Platform is required"}
    if not content:
        return {"error": "Content is required"}
    if not scheduled_at_str:
        return {"error": "scheduled_at is required"}

    # Parse scheduled_at
    try:
        scheduled_at = datetime.fromisoformat(scheduled_at_str)
    except (ValueError, TypeError):
        return {"error": "Invalid scheduled_at format. Use ISO format."}

    # Validate content
    try:
        adapter = get_adapter(platform)
        validation = adapter.validate_content(content)
        if not validation["valid"]:
            return {"error": f"Content validation failed: {'; '.join(validation['errors'])}"}
    except ValueError as e:
        return {"error": str(e)}

    return await schedule_post(user_id, platform, content, scheduled_at, timezone_str, media_urls)


# ============ MARK MANUALLY PUBLISHED ============ #

async def handle_mark_published(post_id: str, user_id: str):
    """Mark a manual-assisted post as published."""
    return await mark_manually_published(post_id, user_id)


# ============ RETRY FAILED ============ #

async def handle_retry_post(post_id: str, user_id: str):
    """Retry a failed post."""
    return await retry_failed_post(post_id, user_id)


# ============ POST HISTORY ============ #

async def handle_get_post_history(user_id: str, platform: str = None, status: str = None, publish_type: str = None, limit: int = 50, offset: int = 0):
    """Get post history with filters."""
    return await get_post_history(user_id, platform, status, publish_type, limit, offset)


async def handle_get_post_detail(post_id: str, user_id: str):
    """Get single post detail."""
    post = await get_post_by_id(post_id, user_id)
    if not post:
        return {"error": "Post not found"}
    return post


async def handle_update_post_status(post_id: str, user_id: str, data: dict):
    """Update post status."""
    status = data.get("status", "")
    if not status:
        return {"error": "Status is required"}
    if status not in VALID_POST_STATUSES:
        return {"error": f"Invalid status. Must be one of: {', '.join(VALID_POST_STATUSES)}"}

    kwargs = {}
    if "failure_reason" in data:
        kwargs["failure_reason"] = data["failure_reason"]
    if "platform_post_id" in data:
        kwargs["platform_post_id"] = data["platform_post_id"]

    return await update_post_status(post_id, user_id, status, **kwargs)


async def handle_delete_post(post_id: str, user_id: str):
    """Delete a post history entry."""
    return await delete_post_history_entry(post_id, user_id)


# ============ PLATFORM CATALOG ============ #

async def handle_get_catalog():
    """Get platform catalog."""
    return await get_platform_catalog()


async def handle_update_catalog(platform_name: str, data: dict):
    """Update platform catalog (admin only)."""
    return await update_platform_catalog(platform_name, data)


# ============ DASHBOARD STATS ============ #

async def handle_get_stats(user_id: str):
    """Get publishing statistics."""
    return await get_publishing_stats(user_id)


# ============ MANUAL PUBLISH PAYLOAD ============ #

async def handle_get_manual_payload(platform: str, data: dict):
    """Get manual publishing payload (content + instructions)."""
    content = data.get("content", "").strip()
    media_urls = data.get("media_urls", [])

    if not content:
        return {"error": "Content is required"}

    try:
        adapter = get_adapter(platform)
        return adapter.generate_manual_publish_payload(content, media_urls)
    except ValueError as e:
        return {"error": str(e)}
