"""
Analytics service - provides detailed post analytics, AI performance insights,
and platform intelligence for the publishing dashboard.
"""

import re
from datetime import datetime, timezone, timedelta
from bson import ObjectId
from app.database import db

post_history_collection = db["post_history"]


async def get_posts_by_status(user_id: str, status: str, limit: int = 50) -> list:
    """Get posts filtered by status with analytics data."""
    query = {"user_id": user_id}
    if status == "published":
        query["status"] = {"$in": ["posted", "manually_published"]}
    elif status == "pending_manual":
        query["status"] = {"$in": ["ready_to_publish", "awaiting_manual_publish"]}
    else:
        query["status"] = status

    cursor = post_history_collection.find(query).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)

    results = []
    for doc in docs:
        post = {
            "id": str(doc["_id"]),
            "unique_post_id": doc.get("unique_post_id", ""),
            "platform": doc.get("platform", ""),
            "content": doc.get("content", ""),
            "content_preview": doc.get("content_preview", ""),
            "status": doc.get("status", ""),
            "publish_type": doc.get("publish_type", ""),
            "scheduled_at": doc.get("scheduled_at").isoformat() if doc.get("scheduled_at") else None,
            "posted_at": doc.get("posted_at").isoformat() if doc.get("posted_at") else None,
            "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
            "failure_reason": doc.get("failure_reason"),
            # Only show real analytics if stored — never fabricate
            "analytics": doc.get("analytics_snapshot") or None,
        }
        results.append(post)

    return results


async def get_post_detail_analytics(user_id: str, post_id: str) -> dict:
    """Get detailed analytics for a single post including AI insights."""
    doc = await post_history_collection.find_one({
        "_id": ObjectId(post_id),
        "user_id": user_id,
    })
    if not doc:
        return {"error": "Post not found"}

    analytics = doc.get("analytics_snapshot") or None
    insights = _generate_ai_insights(doc, analytics) if analytics else _generate_content_insights(doc)

    return {
        "id": str(doc["_id"]),
        "unique_post_id": doc.get("unique_post_id", ""),
        "platform": doc.get("platform", ""),
        "content": doc.get("content", ""),
        "status": doc.get("status", ""),
        "publish_type": doc.get("publish_type", ""),
        "scheduled_at": doc.get("scheduled_at").isoformat() if doc.get("scheduled_at") else None,
        "posted_at": doc.get("posted_at").isoformat() if doc.get("posted_at") else None,
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
        "failure_reason": doc.get("failure_reason"),
        "analytics": analytics,
        "insights": insights,
    }


async def get_platform_performance(user_id: str) -> list:
    """Get performance breakdown by platform."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {
            "_id": "$platform",
            "total": {"$sum": 1},
            "posted": {"$sum": {"$cond": [{"$in": ["$status", ["posted", "manually_published"]]}, 1, 0]}},
            "failed": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
            "scheduled": {"$sum": {"$cond": [{"$eq": ["$status", "scheduled"]}, 1, 0]}},
        }},
        {"$sort": {"total": -1}},
    ]
    results = await post_history_collection.aggregate(pipeline).to_list(length=10)

    platforms = []
    for r in results:
        total = r["total"]
        posted = r["posted"]
        success_rate = round((posted / total * 100), 1) if total > 0 else 0
        platforms.append({
            "platform": r["_id"],
            "total": total,
            "posted": posted,
            "failed": r["failed"],
            "scheduled": r["scheduled"],
            "success_rate": success_rate,
            "reliability": "high" if success_rate >= 80 else "medium" if success_rate >= 50 else "low",
        })

    return platforms


async def get_posting_timeline(user_id: str, days: int = 14) -> list:
    """Get posting activity timeline for the last N days."""
    start_date = datetime.now(timezone.utc) - timedelta(days=days)

    pipeline = [
        {"$match": {"user_id": user_id, "created_at": {"$gte": start_date}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
            "count": {"$sum": 1},
            "posted": {"$sum": {"$cond": [{"$in": ["$status", ["posted", "manually_published"]]}, 1, 0]}},
            "failed": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]
    results = await post_history_collection.aggregate(pipeline).to_list(length=days)
    return [{"date": r["_id"], "count": r["count"], "posted": r["posted"], "failed": r["failed"]} for r in results]


# ============ MOCK ANALYTICS (until real platform APIs provide data) ============ #

def _generate_content_insights(doc: dict) -> list:
    """Generate content-quality insights without engagement data."""
    insights = []
    content = doc.get("content", "")
    platform = doc.get("platform", "")

    # Hook analysis
    first_line = content.split("\n")[0] if content else ""
    if len(first_line) < 80 and first_line:
        insights.append({"type": "positive", "category": "Hook", "text": "Strong opening hook — concise first line grabs attention quickly."})
    elif len(first_line) > 150:
        insights.append({"type": "improvement", "category": "Hook", "text": "Opening line is long. Shorter hooks tend to perform better on feeds."})

    # Hashtag analysis
    hashtag_count = content.count("#")
    if platform in ("linkedin", "instagram") and hashtag_count > 0:
        insights.append({"type": "positive", "category": "Hashtags", "text": f"Used {hashtag_count} hashtags — good for discoverability on {platform}."})
    elif platform in ("linkedin", "instagram") and hashtag_count == 0:
        insights.append({"type": "improvement", "category": "Hashtags", "text": f"No hashtags used. Adding relevant hashtags improves reach on {platform}."})

    # Analytics note
    insights.append({"type": "neutral", "category": "Analytics", "text": "Engagement data unavailable — manual posting doesn't provide API-level analytics. Connect platform API for real metrics."})

    return insights


def _generate_mock_analytics(doc: dict) -> dict:
    """DEPRECATED — no longer used. Returns None to avoid fake data."""
    return None


def _generate_ai_insights(doc: dict, analytics: dict) -> list:
    """Generate AI-powered performance insights for a post."""
    if not analytics:
        return []

    insights = []
    content = doc.get("content", "")
    platform = doc.get("platform", "")
    engagement_rate = analytics.get("engagement_rate", 0)

    # Hook analysis
    first_line = content.split("\n")[0] if content else ""
    if len(first_line) < 80 and first_line:
        insights.append({
            "type": "positive",
            "category": "Hook",
            "text": "Strong opening hook — concise first line grabs attention quickly.",
        })
    elif len(first_line) > 150:
        insights.append({
            "type": "improvement",
            "category": "Hook",
            "text": "Opening line is long. Shorter hooks tend to perform better on feeds.",
        })

    # Engagement analysis
    if engagement_rate > 5:
        insights.append({
            "type": "positive",
            "category": "Engagement",
            "text": f"Excellent engagement rate ({engagement_rate}%). Content resonated well with audience.",
        })
    elif engagement_rate > 2:
        insights.append({
            "type": "neutral",
            "category": "Engagement",
            "text": f"Solid engagement rate ({engagement_rate}%). Above average for {platform}.",
        })
    else:
        insights.append({
            "type": "improvement",
            "category": "Engagement",
            "text": f"Low engagement rate ({engagement_rate}%). Consider stronger CTAs or questions.",
        })

    # Hashtag analysis
    hashtag_count = content.count("#")
    if platform in ("linkedin", "instagram") and hashtag_count > 0:
        insights.append({
            "type": "positive",
            "category": "Hashtags",
            "text": f"Used {hashtag_count} hashtags — good for discoverability on {platform}.",
        })
    elif platform in ("linkedin", "instagram") and hashtag_count == 0:
        insights.append({
            "type": "improvement",
            "category": "Hashtags",
            "text": f"No hashtags used. Adding relevant hashtags improves reach on {platform}.",
        })

    # Content length analysis
    if platform == "twitter" and len(content) <= 280:
        insights.append({
            "type": "positive",
            "category": "Length",
            "text": "Content fits single tweet format — optimal for quick engagement.",
        })
    elif platform == "linkedin" and 500 < len(content) < 2000:
        insights.append({
            "type": "positive",
            "category": "Length",
            "text": "Ideal length for LinkedIn — detailed enough to provide value without losing readers.",
        })

    # Posting time analysis
    posted_at = doc.get("posted_at")
    if posted_at:
        hour = posted_at.hour if hasattr(posted_at, 'hour') else 12
        if 8 <= hour <= 10 or 17 <= hour <= 19:
            insights.append({
                "type": "positive",
                "category": "Timing",
                "text": "Posted during peak activity hours — maximizes initial visibility.",
            })
        elif 0 <= hour <= 5:
            insights.append({
                "type": "improvement",
                "category": "Timing",
                "text": "Posted during low-activity hours. Consider scheduling for 8-10 AM or 5-7 PM.",
            })

    return insights
