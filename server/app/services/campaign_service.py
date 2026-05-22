"""
Campaign Studio service layer — all MongoDB operations for campaigns.
Uses existing TrendZo database, separate collections.
"""

from datetime import datetime, timezone
from bson import ObjectId
from app.database import db

# ── Collections (inside existing TrendZo DB) ─────────────────────────────────
campaigns_collection       = db["campaigns"]
campaign_days_collection   = db["campaign_days"]
campaign_content_collection = db["campaign_content"]
campaign_activity_collection = db["campaign_activity"]


# ── Serializer ────────────────────────────────────────────────────────────────

def serialize_campaign(doc: dict) -> dict:
    """Convert MongoDB doc to JSON-safe dict with computed fields."""
    now = datetime.now(timezone.utc)

    start = doc.get("start_date")
    end   = doc.get("end_date")

    # Compute days remaining and progress
    days_remaining = None
    progress_percent = None
    if start and end:
        try:
            start_dt = datetime.fromisoformat(start)
            end_dt   = datetime.fromisoformat(end)
            total_days = max((end_dt - start_dt).days, 1)
            elapsed    = max((now.replace(tzinfo=None) - start_dt).days, 0)
            days_remaining   = max((end_dt - now.replace(tzinfo=None)).days, 0)
            progress_percent = min(int(elapsed / total_days * 100), 100)
        except Exception:
            pass

    return {
        "id": str(doc["_id"]),
        "user_id": doc.get("user_id", ""),
        "campaign_name": doc.get("campaign_name", ""),
        "campaign_goal": doc.get("campaign_goal", ""),
        "campaign_type": doc.get("campaign_type", ""),
        "target_audience": doc.get("target_audience", ""),
        "duration": doc.get("duration", 0),
        "selected_platforms": doc.get("selected_platforms", []),
        "posting_frequency": doc.get("posting_frequency", ""),
        "tone": doc.get("tone", ""),
        "cta_goal": doc.get("cta_goal", ""),
        "start_date": doc.get("start_date"),
        "end_date": doc.get("end_date"),
        "campaign_status": doc.get("campaign_status", "draft"),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
        "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
        "days_remaining": days_remaining,
        "progress_percent": progress_percent,
    }


# ── CRUD Operations ───────────────────────────────────────────────────────────

async def create_campaign(user_id: str, data: dict) -> dict:
    """Create a new campaign."""
    now = datetime.now(timezone.utc)

    doc = {
        "user_id": user_id,
        "campaign_name": data["campaign_name"],
        "campaign_goal": data["campaign_goal"],
        "campaign_type": data["campaign_type"],
        "target_audience": data["target_audience"],
        "duration": data["duration"],
        "selected_platforms": data["selected_platforms"],
        "posting_frequency": data["posting_frequency"],
        "tone": data["tone"],
        "cta_goal": data["cta_goal"],
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "campaign_status": "draft",
        "created_at": now,
        "updated_at": now,
    }

    result = await campaigns_collection.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Log activity
    await log_activity(user_id, str(result.inserted_id), "created", f"Campaign '{data['campaign_name']}' created")

    return serialize_campaign(doc)


async def get_campaigns(user_id: str, status: str = None) -> list:
    """Get all campaigns for a user, optionally filtered by status."""
    query = {"user_id": user_id}
    if status:
        query["campaign_status"] = status

    cursor = campaigns_collection.find(query).sort("created_at", -1)
    docs = await cursor.to_list(length=100)
    return [serialize_campaign(doc) for doc in docs]


async def get_campaign(campaign_id: str, user_id: str) -> dict:
    """Get a single campaign by ID."""
    doc = await campaigns_collection.find_one({
        "_id": ObjectId(campaign_id),
        "user_id": user_id,
    })
    if not doc:
        return None
    return serialize_campaign(doc)


async def update_campaign(campaign_id: str, user_id: str, data: dict) -> dict:
    """Update a campaign."""
    update_fields = {k: v for k, v in data.items() if v is not None}
    update_fields["updated_at"] = datetime.now(timezone.utc)

    result = await campaigns_collection.find_one_and_update(
        {"_id": ObjectId(campaign_id), "user_id": user_id},
        {"$set": update_fields},
        return_document=True,
    )
    if not result:
        return None

    await log_activity(user_id, campaign_id, "updated", f"Campaign updated")
    return serialize_campaign(result)


async def delete_campaign(campaign_id: str, user_id: str) -> bool:
    """Delete a campaign and its related data."""
    result = await campaigns_collection.delete_one({
        "_id": ObjectId(campaign_id),
        "user_id": user_id,
    })
    if result.deleted_count == 0:
        return False

    # Clean up related collections
    await campaign_days_collection.delete_many({"campaign_id": campaign_id})
    await campaign_content_collection.delete_many({"campaign_id": campaign_id})
    await campaign_activity_collection.delete_many({"campaign_id": campaign_id})

    return True


async def update_campaign_status(campaign_id: str, user_id: str, status: str) -> dict:
    """Update campaign status only."""
    return await update_campaign(campaign_id, user_id, {"campaign_status": status})


async def get_campaign_stats(user_id: str) -> dict:
    """Get aggregate campaign statistics for a user."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {
            "_id": "$campaign_status",
            "count": {"$sum": 1},
        }},
    ]
    results = await campaigns_collection.aggregate(pipeline).to_list(length=10)
    stats = {r["_id"]: r["count"] for r in results}

    total = sum(stats.values())
    return {
        "total": total,
        "draft": stats.get("draft", 0),
        "active": stats.get("active", 0),
        "paused": stats.get("paused", 0),
        "completed": stats.get("completed", 0),
        "cancelled": stats.get("cancelled", 0),
    }


# ── Activity Logging ──────────────────────────────────────────────────────────

async def log_activity(user_id: str, campaign_id: str, action: str, description: str):
    """Log campaign activity."""
    await campaign_activity_collection.insert_one({
        "user_id": user_id,
        "campaign_id": campaign_id,
        "action": action,
        "description": description,
        "created_at": datetime.now(timezone.utc),
    })


async def get_campaign_activity(campaign_id: str, user_id: str, limit: int = 20) -> list:
    """Get activity log for a campaign."""
    cursor = campaign_activity_collection.find({
        "campaign_id": campaign_id,
        "user_id": user_id,
    }).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [
        {
            "id": str(d["_id"]),
            "action": d.get("action"),
            "description": d.get("description"),
            "created_at": d.get("created_at").isoformat() if d.get("created_at") else None,
        }
        for d in docs
    ]
