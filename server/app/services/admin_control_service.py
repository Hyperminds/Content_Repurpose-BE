"""
Super Admin Control Center — global AI controls, admin logs, impersonation, predictive insights.
"""

from datetime import datetime, timezone, timedelta
from bson import ObjectId
from app.database import db

admin_logs_collection = db["admin_logs"]
system_settings_collection = db["system_settings"]
users_collection = db["users"]
generation_logs_collection = db["generation_logs"]
post_history_collection = db["post_history"]
campaigns_collection = db["campaigns"]


# ── ADMIN ACTION LOGGING ──────────────────────────────────────────────────────

async def log_admin_action(admin_id: str, action: str, target: str, details: str = ""):
    """Log every admin action for audit trail."""
    await admin_logs_collection.insert_one({
        "admin_id": admin_id,
        "action": action,
        "target": target,
        "details": details,
        "timestamp": datetime.now(timezone.utc),
    })


async def get_admin_logs(limit: int = 100) -> list:
    """Get recent admin action logs."""
    cursor = admin_logs_collection.find().sort("timestamp", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [
        {
            "id": str(d["_id"]),
            "admin_id": d.get("admin_id", ""),
            "action": d.get("action", ""),
            "target": d.get("target", ""),
            "details": d.get("details", ""),
            "timestamp": d.get("timestamp").isoformat() if d.get("timestamp") else None,
        }
        for d in docs
    ]


# ── GLOBAL AI CONTROL CENTER ──────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "ai_enabled": True,
    "posting_enabled": True,
    "campaign_generation_enabled": True,
    "moderation_sensitivity": "medium",  # low, medium, high
    "max_tokens_per_user_daily": 100000,
    "max_generations_per_user_daily": 50,
    "ai_model": "openai/gpt-4o-mini",
    "global_rate_limit": 100,  # requests per minute
}


async def get_system_settings() -> dict:
    """Get current system settings."""
    doc = await system_settings_collection.find_one({"_id": "global"})
    if not doc:
        return DEFAULT_SETTINGS
    settings = {k: v for k, v in doc.items() if k != "_id"}
    return {**DEFAULT_SETTINGS, **settings}


async def update_system_settings(updates: dict) -> dict:
    """Update system settings."""
    allowed = set(DEFAULT_SETTINGS.keys())
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return {"error": "No valid settings to update"}

    await system_settings_collection.update_one(
        {"_id": "global"},
        {"$set": filtered},
        upsert=True,
    )
    return await get_system_settings()


# ── USER IMPERSONATION ────────────────────────────────────────────────────────

async def get_user_for_impersonation(user_id: str) -> dict:
    """Get user data for impersonation (view-as-user)."""
    user = await users_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        return None
    return {
        "id": str(user["_id"]),
        "name": user.get("name", ""),
        "email": user.get("email", ""),
        "role": user.get("role", "member"),
        "moderation_status": user.get("moderation_status", "active"),
        "moderation_flags": user.get("moderation_flags", 0),
        "created_at": user.get("created_at").isoformat() if user.get("created_at") else None,
    }


async def generate_impersonation_token(user_id: str) -> str:
    """Generate a temporary JWT for viewing as another user."""
    from app.utils.jwt_handler import create_access_token
    user = await users_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        return None
    token = create_access_token({
        "user_id": str(user["_id"]),
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "role": user.get("role", "member"),
        "impersonated": True,
    })
    return token


# ── PREDICTIVE INSIGHTS ───────────────────────────────────────────────────────

async def get_predictive_insights() -> dict:
    """Generate predictive insights from platform data."""
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    # Token usage trend (last 7 days vs previous 7 days)
    this_week_tokens = await _sum_tokens_since(week_ago)
    prev_week_tokens = await _sum_tokens_since(now - timedelta(days=14), week_ago)
    token_trend = "increasing" if this_week_tokens > prev_week_tokens else "decreasing" if this_week_tokens < prev_week_tokens else "stable"
    token_change_pct = round(((this_week_tokens - prev_week_tokens) / max(prev_week_tokens, 1)) * 100, 1)

    # User growth
    new_users_week = await users_collection.count_documents({"created_at": {"$gte": week_ago}})
    total_users = await users_collection.count_documents({})

    # Posting velocity
    posts_today = await post_history_collection.count_documents({"created_at": {"$gte": day_ago}})
    posts_week = await post_history_collection.count_documents({"created_at": {"$gte": week_ago}})

    # Top token consumers
    top_users_pipeline = [
        {"$match": {"generated_at": {"$gte": week_ago}}},
        {"$group": {"_id": "$user_id", "tokens": {"$sum": "$total_tokens"}, "count": {"$sum": 1}}},
        {"$sort": {"tokens": -1}},
        {"$limit": 5},
    ]
    top_users = await generation_logs_collection.aggregate(top_users_pipeline).to_list(5)

    # Suspicious activity (users with >3 flags)
    suspicious = await users_collection.count_documents({"moderation_flags": {"$gte": 2}})

    # Campaign performance prediction
    active_campaigns = await campaigns_collection.count_documents({"campaign_status": "active"})

    # Revenue estimation (based on token usage at $20/user/month average)
    estimated_mrr = total_users * 20  # Simplified

    return {
        "token_trend": token_trend,
        "token_change_pct": token_change_pct,
        "this_week_tokens": this_week_tokens,
        "prev_week_tokens": prev_week_tokens,
        "new_users_week": new_users_week,
        "total_users": total_users,
        "posts_today": posts_today,
        "posts_week": posts_week,
        "posting_velocity": round(posts_week / 7, 1) if posts_week else 0,
        "top_token_consumers": [
            {"user_id": u["_id"], "tokens": u["tokens"], "generations": u["count"]}
            for u in top_users
        ],
        "suspicious_users": suspicious,
        "active_campaigns": active_campaigns,
        "estimated_mrr": estimated_mrr,
        "growth_rate": round((new_users_week / max(total_users - new_users_week, 1)) * 100, 1),
        "predictions": [
            {"type": "growth", "confidence": "high" if new_users_week > 0 else "low",
             "text": f"User base growing at {round((new_users_week / max(total_users, 1)) * 100, 1)}% weekly."},
            {"type": "cost", "confidence": "medium",
             "text": f"AI costs {'increasing' if token_trend == 'increasing' else 'stable'}. {token_change_pct:+.1f}% week-over-week."},
            {"type": "engagement", "confidence": "medium",
             "text": f"Posting velocity: {round(posts_week / 7, 1)} posts/day average."},
            {"type": "risk", "confidence": "high" if suspicious > 0 else "low",
             "text": f"{suspicious} users flagged for potential abuse." if suspicious > 0 else "No suspicious activity detected."},
        ],
    }


async def _sum_tokens_since(start, end=None) -> int:
    query = {"generated_at": {"$gte": start}}
    if end:
        query["generated_at"]["$lt"] = end
    pipeline = [{"$match": query}, {"$group": {"_id": None, "total": {"$sum": "$total_tokens"}}}]
    result = await generation_logs_collection.aggregate(pipeline).to_list(1)
    return int(result[0]["total"]) if result else 0


# ── ADVANCED ANALYTICS ────────────────────────────────────────────────────────

async def get_advanced_analytics() -> dict:
    """Revenue estimation, cost tracking, platform heatmap."""
    now = datetime.now(timezone.utc)

    # Total cost
    cost_pipeline = [{"$group": {"_id": None, "total": {"$sum": "$estimated_cost"}}}]
    cost_result = await generation_logs_collection.aggregate(cost_pipeline).to_list(1)
    total_cost = round(cost_result[0]["total"] if cost_result else 0, 4)

    # Platform usage heatmap (by hour of day)
    heatmap_pipeline = [
        {"$group": {
            "_id": {"platform": "$platform", "hour": {"$hour": "$generated_at"}},
            "count": {"$sum": 1},
        }},
        {"$sort": {"count": -1}},
    ]
    heatmap_results = await generation_logs_collection.aggregate(heatmap_pipeline).to_list(200)

    # Build heatmap
    heatmap = {}
    for r in heatmap_results:
        platform = r["_id"]["platform"]
        hour = r["_id"]["hour"]
        if platform not in heatmap:
            heatmap[platform] = {}
        heatmap[platform][hour] = r["count"]

    # Monthly cost trend
    monthly_pipeline = [
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m", "date": "$generated_at"}},
            "cost": {"$sum": "$estimated_cost"},
            "tokens": {"$sum": "$total_tokens"},
        }},
        {"$sort": {"_id": 1}},
    ]
    monthly = await generation_logs_collection.aggregate(monthly_pipeline).to_list(12)

    total_users = await users_collection.count_documents({})

    return {
        "total_ai_cost": total_cost,
        "estimated_mrr": total_users * 20,
        "estimated_arr": total_users * 20 * 12,
        "cost_per_user": round(total_cost / max(total_users, 1), 4),
        "platform_heatmap": heatmap,
        "monthly_trend": [{"month": m["_id"], "cost": round(m["cost"], 4), "tokens": m["tokens"]} for m in monthly],
    }
