"""
AI Resource Usage Tracking Service.
Tracks token usage, generation time, and estimated costs for every AI generation.
"""

import time
from datetime import datetime, timezone
from bson import ObjectId
from app.database import db

generation_logs_collection = db["generation_logs"]

# ============ AVAILABLE MODELS FOR USER SELECTION ============ #
# Ordered by cost (cheapest first)
AVAILABLE_MODELS = [
    {
        "id": "openai/gpt-4o-mini",
        "name": "GPT-4o Mini",
        "provider": "OpenAI",
        "description": "Best quality. Fast and reliable.",
        "input_per_1m": 0.15,
        "output_per_1m": 0.60,
        "est_cost_per_generation": 0.003,
        "badge": "Recommended",
        "badge_color": "#10B981",
    },
    {
        "id": "openai/gpt-4.1-nano",
        "name": "GPT-4.1 Nano",
        "provider": "OpenAI",
        "description": "Faster and cheaper than 4o-mini.",
        "input_per_1m": 0.10,
        "output_per_1m": 0.40,
        "est_cost_per_generation": 0.002,
        "badge": "Fast",
        "badge_color": "#06B6D4",
    },
    {
        "id": "mistralai/mistral-small-3.2-24b-instruct",
        "name": "Mistral Small 3.2",
        "provider": "Mistral",
        "description": "Affordable European model. Great quality.",
        "input_per_1m": 0.10,
        "output_per_1m": 0.30,
        "est_cost_per_generation": 0.0012,
        "badge": "Cheapest",
        "badge_color": "#F59E0B",
    },
]

# ============ MODEL PRICING (per 1M tokens) ============ #
MODEL_PRICING = {m["id"]: {"input_per_1m": m["input_per_1m"], "output_per_1m": m["output_per_1m"]}
                 for m in AVAILABLE_MODELS}
MODEL_PRICING["default"] = {"input_per_1m": 0.15, "output_per_1m": 0.60}


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate estimated cost in USD."""
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])
    input_cost = (prompt_tokens / 1_000_000) * pricing["input_per_1m"]
    output_cost = (completion_tokens / 1_000_000) * pricing["output_per_1m"]
    return round(input_cost + output_cost, 8)


async def log_generation(
    user_id: str,
    platform: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    generation_time_ms: int,
    content_preview: str = "",
    history_id: str = None,
    organization_id: str = "default",
    campaign_id: str = None,
):
    """
    Store a generation log entry.

    organization_id and campaign_id are optional, backward-compatible extensions
    for enterprise-grade, multi-tenant usage tracking. Existing callers that omit
    them get safe defaults.
    """
    total_tokens = prompt_tokens + completion_tokens
    estimated_cost = calculate_cost(model, prompt_tokens, completion_tokens)

    doc = {
        "user_id": user_id,
        "organization_id": organization_id or "default",
        "campaign_id": campaign_id,
        "history_id": history_id,
        "platform": platform,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": estimated_cost,
        "generation_time_ms": generation_time_ms,
        "content_preview": content_preview[:100] if content_preview else "",
        "generated_at": datetime.now(timezone.utc),
    }

    result = await generation_logs_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


def serialize_log(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "user_id": doc.get("user_id"),
        "platform": doc.get("platform"),
        "model": doc.get("model"),
        "prompt_tokens": doc.get("prompt_tokens", 0),
        "completion_tokens": doc.get("completion_tokens", 0),
        "total_tokens": doc.get("total_tokens", 0),
        "estimated_cost": doc.get("estimated_cost", 0),
        "generation_time_ms": doc.get("generation_time_ms", 0),
        "content_preview": doc.get("content_preview", ""),
        "generated_at": doc.get("generated_at").isoformat() if doc.get("generated_at") else None,
    }


async def get_usage_summary(user_id: str) -> dict:
    """Get aggregate AI usage stats for a user."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {
            "_id": None,
            "total_tokens": {"$sum": "$total_tokens"},
            "total_prompt_tokens": {"$sum": "$prompt_tokens"},
            "total_completion_tokens": {"$sum": "$completion_tokens"},
            "total_cost": {"$sum": "$estimated_cost"},
            "total_generations": {"$sum": 1},
            "avg_tokens": {"$avg": "$total_tokens"},
            "avg_generation_time": {"$avg": "$generation_time_ms"},
        }},
    ]
    results = await generation_logs_collection.aggregate(pipeline).to_list(1)
    if not results:
        return {
            "total_tokens": 0, "total_prompt_tokens": 0, "total_completion_tokens": 0,
            "total_cost": 0, "total_generations": 0, "avg_tokens": 0, "avg_generation_time": 0,
        }
    r = results[0]
    return {
        "total_tokens": r.get("total_tokens", 0),
        "total_prompt_tokens": r.get("total_prompt_tokens", 0),
        "total_completion_tokens": r.get("total_completion_tokens", 0),
        "total_cost": round(r.get("total_cost", 0), 6),
        "total_generations": r.get("total_generations", 0),
        "avg_tokens": int(r.get("avg_tokens", 0)),
        "avg_generation_time": int(r.get("avg_generation_time", 0)),
    }


async def get_platform_breakdown(user_id: str) -> list:
    """Get token usage broken down by platform."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {
            "_id": "$platform",
            "total_tokens": {"$sum": "$total_tokens"},
            "total_cost": {"$sum": "$estimated_cost"},
            "count": {"$sum": 1},
            "avg_tokens": {"$avg": "$total_tokens"},
        }},
        {"$sort": {"total_tokens": -1}},
    ]
    results = await generation_logs_collection.aggregate(pipeline).to_list(10)
    return [
        {
            "platform": r["_id"],
            "total_tokens": r.get("total_tokens", 0),
            "total_cost": round(r.get("total_cost", 0), 6),
            "count": r.get("count", 0),
            "avg_tokens": int(r.get("avg_tokens", 0)),
        }
        for r in results
    ]


async def get_recent_logs(user_id: str, limit: int = 20) -> list:
    """Get recent generation logs."""
    cursor = generation_logs_collection.find({"user_id": user_id}).sort("generated_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [serialize_log(doc) for doc in docs]


async def get_efficiency_insights(user_id: str) -> dict:
    """Calculate AI efficiency metrics."""
    summary = await get_usage_summary(user_id)
    breakdown = await get_platform_breakdown(user_id)

    avg_tokens = summary.get("avg_tokens", 0)

    # Efficiency classification
    if avg_tokens < 500:
        efficiency = "Highly Efficient"
        efficiency_color = "success"
    elif avg_tokens < 1000:
        efficiency = "Balanced"
        efficiency_color = "warning"
    else:
        efficiency = "Token Heavy"
        efficiency_color = "error"

    # Most/least expensive platform
    most_expensive = max(breakdown, key=lambda x: x["total_cost"]) if breakdown else None
    most_efficient = min(breakdown, key=lambda x: x["avg_tokens"]) if breakdown else None

    return {
        "efficiency_label": efficiency,
        "efficiency_color": efficiency_color,
        "avg_tokens_per_generation": avg_tokens,
        "most_expensive_platform": most_expensive["platform"] if most_expensive else None,
        "most_efficient_platform": most_efficient["platform"] if most_efficient else None,
        "total_cost_usd": summary.get("total_cost", 0),
    }


# ════════════════════════════════════════════════════════════════════════════
#  ENTERPRISE AGGREGATIONS
#  Daily / Monthly / Organization / User usage rollups.
#  All filters are optional and composable (user_id, organization_id, campaign_id).
# ════════════════════════════════════════════════════════════════════════════

def _build_match(user_id: str = None, organization_id: str = None, campaign_id: str = None) -> dict:
    """Compose a MongoDB match stage from optional dimensions."""
    match = {}
    if user_id:
        match["user_id"] = user_id
    if organization_id:
        match["organization_id"] = organization_id
    if campaign_id:
        match["campaign_id"] = campaign_id
    return match


async def get_daily_usage(
    user_id: str = None,
    organization_id: str = None,
    campaign_id: str = None,
    days: int = 30,
) -> list:
    """
    Daily token + cost rollup for the last `days` days.
    Returns one entry per day, oldest → newest.
    """
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(days=days)
    match = _build_match(user_id, organization_id, campaign_id)
    match["generated_at"] = {"$gte": since}

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$generated_at"}},
            "total_tokens": {"$sum": "$total_tokens"},
            "prompt_tokens": {"$sum": "$prompt_tokens"},
            "completion_tokens": {"$sum": "$completion_tokens"},
            "total_cost": {"$sum": "$estimated_cost"},
            "generations": {"$sum": 1},
            "avg_generation_time_ms": {"$avg": "$generation_time_ms"},
        }},
        {"$sort": {"_id": 1}},
    ]
    results = await generation_logs_collection.aggregate(pipeline).to_list(days + 1)
    return [
        {
            "date": r["_id"],
            "total_tokens": r.get("total_tokens", 0),
            "prompt_tokens": r.get("prompt_tokens", 0),
            "completion_tokens": r.get("completion_tokens", 0),
            "total_cost": round(r.get("total_cost", 0), 6),
            "generations": r.get("generations", 0),
            "avg_generation_time_ms": int(r.get("avg_generation_time_ms", 0)),
        }
        for r in results
    ]


async def get_monthly_usage(
    user_id: str = None,
    organization_id: str = None,
    campaign_id: str = None,
    months: int = 12,
) -> list:
    """Monthly token + cost rollup, oldest → newest (last `months` buckets)."""
    pipeline = [
        {"$match": _build_match(user_id, organization_id, campaign_id)},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m", "date": "$generated_at"}},
            "total_tokens": {"$sum": "$total_tokens"},
            "prompt_tokens": {"$sum": "$prompt_tokens"},
            "completion_tokens": {"$sum": "$completion_tokens"},
            "total_cost": {"$sum": "$estimated_cost"},
            "generations": {"$sum": 1},
        }},
        {"$sort": {"_id": -1}},
        {"$limit": months},
    ]
    results = await generation_logs_collection.aggregate(pipeline).to_list(months)
    # reverse for chronological order
    return [
        {
            "month": r["_id"],
            "total_tokens": r.get("total_tokens", 0),
            "prompt_tokens": r.get("prompt_tokens", 0),
            "completion_tokens": r.get("completion_tokens", 0),
            "total_cost": round(r.get("total_cost", 0), 6),
            "generations": r.get("generations", 0),
        }
        for r in reversed(results)
    ]


async def get_organization_usage(organization_id: str) -> dict:
    """
    Full organization rollup: totals plus breakdowns by user, platform, and model.
    """
    base_match = {"organization_id": organization_id}

    # Totals
    totals_pipeline = [
        {"$match": base_match},
        {"$group": {
            "_id": None,
            "total_tokens": {"$sum": "$total_tokens"},
            "prompt_tokens": {"$sum": "$prompt_tokens"},
            "completion_tokens": {"$sum": "$completion_tokens"},
            "total_cost": {"$sum": "$estimated_cost"},
            "generations": {"$sum": 1},
            "unique_users": {"$addToSet": "$user_id"},
        }},
    ]
    totals_res = await generation_logs_collection.aggregate(totals_pipeline).to_list(1)
    totals = totals_res[0] if totals_res else {}

    # By user
    user_pipeline = [
        {"$match": base_match},
        {"$group": {
            "_id": "$user_id",
            "total_tokens": {"$sum": "$total_tokens"},
            "total_cost": {"$sum": "$estimated_cost"},
            "generations": {"$sum": 1},
        }},
        {"$sort": {"total_tokens": -1}},
        {"$limit": 50},
    ]
    by_user = await generation_logs_collection.aggregate(user_pipeline).to_list(50)

    # By platform
    platform_pipeline = [
        {"$match": base_match},
        {"$group": {
            "_id": "$platform",
            "total_tokens": {"$sum": "$total_tokens"},
            "total_cost": {"$sum": "$estimated_cost"},
            "generations": {"$sum": 1},
        }},
        {"$sort": {"total_tokens": -1}},
    ]
    by_platform = await generation_logs_collection.aggregate(platform_pipeline).to_list(20)

    # By model
    model_pipeline = [
        {"$match": base_match},
        {"$group": {
            "_id": "$model",
            "total_tokens": {"$sum": "$total_tokens"},
            "total_cost": {"$sum": "$estimated_cost"},
            "generations": {"$sum": 1},
        }},
        {"$sort": {"total_cost": -1}},
    ]
    by_model = await generation_logs_collection.aggregate(model_pipeline).to_list(20)

    return {
        "organization_id": organization_id,
        "totals": {
            "total_tokens": totals.get("total_tokens", 0),
            "prompt_tokens": totals.get("prompt_tokens", 0),
            "completion_tokens": totals.get("completion_tokens", 0),
            "total_cost": round(totals.get("total_cost", 0), 6),
            "generations": totals.get("generations", 0),
            "unique_users": len(totals.get("unique_users", [])),
        },
        "by_user": [
            {"user_id": u["_id"], "total_tokens": u.get("total_tokens", 0),
             "total_cost": round(u.get("total_cost", 0), 6), "generations": u.get("generations", 0)}
            for u in by_user
        ],
        "by_platform": [
            {"platform": p["_id"], "total_tokens": p.get("total_tokens", 0),
             "total_cost": round(p.get("total_cost", 0), 6), "generations": p.get("generations", 0)}
            for p in by_platform
        ],
        "by_model": [
            {"model": m["_id"], "total_tokens": m.get("total_tokens", 0),
             "total_cost": round(m.get("total_cost", 0), 6), "generations": m.get("generations", 0)}
            for m in by_model
        ],
    }


async def get_user_usage(user_id: str) -> dict:
    """
    Full per-user rollup: totals plus breakdowns by platform, model, and campaign.
    Richer than get_usage_summary (which stays untouched for backward compat).
    """
    base_match = {"user_id": user_id}

    summary = await get_usage_summary(user_id)
    platforms = await get_platform_breakdown(user_id)

    # By model
    model_pipeline = [
        {"$match": base_match},
        {"$group": {
            "_id": "$model",
            "total_tokens": {"$sum": "$total_tokens"},
            "total_cost": {"$sum": "$estimated_cost"},
            "generations": {"$sum": 1},
        }},
        {"$sort": {"total_cost": -1}},
    ]
    by_model = await generation_logs_collection.aggregate(model_pipeline).to_list(20)

    # By campaign (only records that carry a campaign_id)
    campaign_pipeline = [
        {"$match": {"user_id": user_id, "campaign_id": {"$ne": None}}},
        {"$group": {
            "_id": "$campaign_id",
            "total_tokens": {"$sum": "$total_tokens"},
            "total_cost": {"$sum": "$estimated_cost"},
            "generations": {"$sum": 1},
        }},
        {"$sort": {"total_tokens": -1}},
        {"$limit": 50},
    ]
    by_campaign = await generation_logs_collection.aggregate(campaign_pipeline).to_list(50)

    return {
        "user_id": user_id,
        "totals": summary,
        "by_platform": platforms,
        "by_model": [
            {"model": m["_id"], "total_tokens": m.get("total_tokens", 0),
             "total_cost": round(m.get("total_cost", 0), 6), "generations": m.get("generations", 0)}
            for m in by_model
        ],
        "by_campaign": [
            {"campaign_id": c["_id"], "total_tokens": c.get("total_tokens", 0),
             "total_cost": round(c.get("total_cost", 0), 6), "generations": c.get("generations", 0)}
            for c in by_campaign
        ],
    }
