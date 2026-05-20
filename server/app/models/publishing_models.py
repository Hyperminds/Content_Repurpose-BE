"""
Publishing system models for multi-platform content publishing.

PostHistory document:
{
    "_id": ObjectId,
    "unique_post_id": str (e.g. "REP-LINK-0001"),
    "tracking_id": str,
    "user_id": str,
    "platform": str,
    "content": str,
    "content_preview": str (first 100 chars),
    "media_urls": [str],
    "publish_type": "instant" | "scheduled" | "manual_assisted",
    "status": str,
    "scheduled_at": datetime | None,
    "posted_at": datetime | None,
    "manually_published_at": datetime | None,
    "failure_reason": str | None,
    "platform_post_id": str | None,
    "analytics_snapshot": dict | None,
    "created_at": datetime,
    "updated_at": datetime
}

PlatformCatalog document:
{
    "_id": ObjectId,
    "platform_name": str,
    "display_name": str,
    "icon": str,
    "enabled": bool,
    "posting_mode": "auto" | "manual_assisted",
    "api_status": "connected" | "disconnected" | "not_configured",
    "oauth_supported": bool,
    "supported_media_types": [str],
    "character_limit": int | None,
    "hashtag_limit": int | None,
    "posting_limits": dict,
    "platform_rules": str
}

ConnectedPlatforms document:
{
    "_id": ObjectId,
    "user_id": str,
    "platform": str,
    "access_token": str (encrypted),
    "refresh_token": str (encrypted),
    "expires_at": datetime | None,
    "connected_at": datetime,
    "status": "active" | "expired" | "revoked"
}
"""

# Valid statuses for post history
VALID_POST_STATUSES = [
    "draft",
    "scheduled",
    "pending",
    "posting",
    "posted",
    "failed",
    "ready_to_publish",
    "awaiting_manual_publish",
    "manually_published",
    "cancelled",
]

# Platform posting modes
PLATFORM_MODES = {
    "linkedin": "auto",
    "instagram": "manual_assisted",
    "reddit": "manual_assisted",
    "medium": "manual_assisted",
    "twitter": "manual_assisted",
    "quora": "manual_assisted",
    "meta": "manual_assisted",
}

# Platform ID prefixes for unique post IDs
PLATFORM_PREFIXES = {
    "linkedin": "LINK",
    "instagram": "INSTA",
    "twitter": "TWIT",
    "reddit": "REDD",
    "medium": "MED",
    "meta": "META",
    "quora": "QUORA",
}
