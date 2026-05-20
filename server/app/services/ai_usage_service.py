"""
AI Resource Usage Tracking Service.
Tracks token usage, generation time, and estimated costs for every AI generation.
Pricing is based on OpenRouter/OpenAI gpt-4o-mini rates.
"""

import time
from datetime import datetime, timezone
from bson import ObjectId
from app.database import db

generation_logs_collection = db["generation_logs"]

# ============ MODEL PRICING (per 1M tokens) ============ #
# gpt-4o-mini pricing via OpenRouter
MODEL_PRICING = {
    "openai/gpt-4o-mini": {
        "input_per_1m": 0.15,   # $0.15 per 1M input tokens
        "output_per_1m": 0.60,  # $0.60 per 1M output tokens
    },
    "openai/gpt-4o": {
        "input_per_1m": 5.00,
        "output_per_1m": 15.00,
    },
    "default": {
        "input_per_1m": 0.15,
        "output_per_1m": 0.60,
    },
}


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
):
    """Store a generation log entry."""
    total_tokens = prompt_tokens + completion_tokens
    estimated_cost = calculate_cost(model, prompt_tokens, completion_tokens)

    doc = {
        "user_id": user_id,
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
