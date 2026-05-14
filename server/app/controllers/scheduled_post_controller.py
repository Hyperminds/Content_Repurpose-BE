from datetime import datetime, timezone
from bson import ObjectId
from app.database import scheduled_posts_collection
from app.models.scheduled_post_model import VALID_STATUSES


def serialize_post(doc):
    """Convert MongoDB document to JSON-serializable dict."""
    return {
        "id": str(doc["_id"]),
        "user_id": doc.get("user_id"),
        "original_content": doc.get("original_content"),
        "platform_versions": doc.get("platform_versions", {}),
        "selected_platforms": doc.get("selected_platforms", []),
        "scheduled_at": doc.get("scheduled_at").isoformat() if doc.get("scheduled_at") else None,
        "timezone": doc.get("timezone", "UTC"),
        "status": doc.get("status", "draft"),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
        "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
    }


async def create_scheduled_post(data: dict, user_id: str):
    """Create a new scheduled post."""
    # Validation
    original_content = data.get("original_content", "").strip()
    if not original_content:
        return {"error": "original_content is required"}

    platform_versions = data.get("platform_versions", {})
    selected_platforms = data.get("selected_platforms", [])
    if not selected_platforms:
        return {"error": "At least one platform must be selected"}

    scheduled_at = data.get("scheduled_at")
    tz = data.get("timezone", "UTC")
    status = data.get("status", "draft")

    if status not in VALID_STATUSES:
        return {"error": f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}"}

    # Parse scheduled_at if provided
    parsed_scheduled_at = None
    if scheduled_at:
        try:
            parsed_scheduled_at = datetime.fromisoformat(scheduled_at)
        except (ValueError, TypeError):
            return {"error": "Invalid scheduled_at format. Use ISO format (e.g. 2026-05-15T10:00:00Z)"}

    now = datetime.now(timezone.utc)

    doc = {
        "user_id": user_id,
        "original_content": original_content,
        "platform_versions": platform_versions,
        "selected_platforms": selected_platforms,
        "scheduled_at": parsed_scheduled_at,
        "timezone": tz,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }

    result = await scheduled_posts_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_post(doc)


async def get_scheduled_posts(user_id: str, status: str | None = None):
    """Get all scheduled posts for a user, optionally filtered by status."""
    query = {"user_id": user_id}
    if status:
        if status not in VALID_STATUSES:
            return {"error": f"Invalid status filter. Must be one of: {', '.join(VALID_STATUSES)}"}
        query["status"] = status

    cursor = scheduled_posts_collection.find(query).sort("scheduled_at", 1)
    docs = await cursor.to_list(length=100)
    return [serialize_post(doc) for doc in docs]


async def update_scheduled_post(post_id: str, user_id: str, data: dict):
    """Update an existing scheduled post."""
    # Verify ownership
    existing = await scheduled_posts_collection.find_one({
        "_id": ObjectId(post_id),
        "user_id": user_id,
    })
    if not existing:
        return {"error": "Scheduled post not found"}

    # Build update fields
    update_fields = {"updated_at": datetime.now(timezone.utc)}

    if "original_content" in data:
        content = data["original_content"].strip()
        if not content:
            return {"error": "original_content cannot be empty"}
        update_fields["original_content"] = content

    if "platform_versions" in data:
        update_fields["platform_versions"] = data["platform_versions"]

    if "selected_platforms" in data:
        if not data["selected_platforms"]:
            return {"error": "At least one platform must be selected"}
        update_fields["selected_platforms"] = data["selected_platforms"]

    if "scheduled_at" in data:
        if data["scheduled_at"]:
            try:
                update_fields["scheduled_at"] = datetime.fromisoformat(data["scheduled_at"])
            except (ValueError, TypeError):
                return {"error": "Invalid scheduled_at format"}
        else:
            update_fields["scheduled_at"] = None

    if "timezone" in data:
        update_fields["timezone"] = data["timezone"]

    if "status" in data:
        if data["status"] not in VALID_STATUSES:
            return {"error": f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}"}
        update_fields["status"] = data["status"]

    await scheduled_posts_collection.update_one(
        {"_id": ObjectId(post_id)},
        {"$set": update_fields}
    )

    updated = await scheduled_posts_collection.find_one({"_id": ObjectId(post_id)})
    return serialize_post(updated)


async def delete_scheduled_post(post_id: str, user_id: str):
    """Delete a scheduled post (only if owned by user)."""
    result = await scheduled_posts_collection.delete_one({
        "_id": ObjectId(post_id),
        "user_id": user_id,
    })
    if result.deleted_count == 0:
        return {"error": "Scheduled post not found"}
    return {"message": "Scheduled post deleted"}
