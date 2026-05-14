"""
ScheduledPost document schema for MongoDB.

{
    "_id": ObjectId,
    "user_id": str,
    "original_content": str,
    "platform_versions": {
        "linkedin": str,
        "twitter": str,
        "instagram": str,
        "reddit": str,
        "medium": str,
        "meta": str,
        "quora": str
    },
    "selected_platforms": ["linkedin", "twitter", ...],
    "scheduled_at": datetime (UTC),
    "timezone": str (e.g. "Asia/Kolkata"),
    "status": "draft" | "scheduled" | "publishing" | "published" | "failed",
    "created_at": datetime,
    "updated_at": datetime
}

Valid statuses:
- draft: saved but not yet scheduled
- scheduled: queued for publishing at scheduled_at
- publishing: currently being published
- published: successfully posted
- failed: publishing attempt failed
"""

VALID_STATUSES = ["draft", "scheduled", "publishing", "published", "failed"]
