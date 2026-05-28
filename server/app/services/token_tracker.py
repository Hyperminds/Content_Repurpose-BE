"""
AI Token Usage Tracking System for TrendZo.
Tracks token consumption per user, platform, campaign, and feature.
Stores historical data for analytics and quota enforcement.
"""

from datetime import datetime, timezone
from app.database import db
from app.services.logger import log

token_usage_collection = db["token_usage"]


async def track_usage(
    user_id: str,
    platform: str,
    feature: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    campaign_id: str = None,
    cost_usd: float = 0.0,
):
    """
    Record a single AI API call's token usage.
    Features: content_generation, trend_analysis, social_presence, campaign, bio_optimization
    """
    doc = {
        "user_id": user_id,
        "platform": platform,
        "feature": feature,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": cost_usd,
        "campaign_id": campaign_id,
        "created_at": datetime.now(timezone.utc),
    }
    await token_usage_collection.insert_one(doc)
    log.ai_call(model, platform, tokens=total_tokens)


async def get_user_usage_summary(user_id: str, days: int = 30) -> dict:
    """Get aggregated token usage for a user over the last N days."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        {"$match": {"user_id": user_id, "created_at": {"$gte": cutoff}}},
        {"$group": {
            "_id": None,
            "total_tokens": {"$sum": "$total_tokens"},
            "total_cost": {"$sum": "$cost_usd"},
            "total_calls": {"$sum": 1},
            "prompt_tokens": {"$sum": "$prompt_tokens"},
            "completion_tokens": {"$sum": "$completion_tokens"},
        }},
    ]
    results = await token_usage_collection.aggregate(pipeline).to_list(length=1)
    if not results:
        return {"total_tokens": 0, "total_cost": 0, "total_calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    r = results[0]
    r.pop("_id", None)
    return r


async def get_usage_by_platform(user_id: str, days: int = 30) -> list:
    """Get token usage broken down by platform."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        {"$match": {"user_id": user_id, "created_at": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$platform",
            "total_tokens": {"$sum": "$total_tokens"},
            "total_cost": {"$sum": "$cost_usd"},
            "calls": {"$sum": 1},
        }},
        {"$sort": {"total_tokens": -1}},
    ]
    results = await token_usage_collection.aggregate(pipeline).to_list(length=20)
    return [{"platform": r["_id"], **{k: v for k, v in r.items() if k != "_id"}} for r in results]


async def get_usage_by_feature(user_id: str, days: int = 30) -> list:
    """Get token usage broken down by feature."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        {"$match": {"user_id": user_id, "created_at": {"$gte": cutoff}}},
        {"$group": {
            "_id": "$feature",
            "total_tokens": {"$sum": "$total_tokens"},
            "calls": {"$sum": 1},
        }},
        {"$sort": {"total_tokens": -1}},
    ]
    results = await token_usage_collection.aggregate(pipeline).to_list(length=20)
    return [{"feature": r["_id"], **{k: v for k, v in r.items() if k != "_id"}} for r in results]


async def check_user_quota(user_id: str, daily_limit: int = 100000) -> dict:
    """Check if user has exceeded their daily token quota."""
    from datetime import timedelta
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    pipeline = [
        {"$match": {"user_id": user_id, "created_at": {"$gte": today_start}}},
        {"$group": {"_id": None, "total": {"$sum": "$total_tokens"}}},
    ]
    results = await token_usage_collection.aggregate(pipeline).to_list(length=1)
    used = results[0]["total"] if results else 0

    return {
        "used_today": used,
        "daily_limit": daily_limit,
        "remaining": max(0, daily_limit - used),
        "exceeded": used >= daily_limit,
    }
