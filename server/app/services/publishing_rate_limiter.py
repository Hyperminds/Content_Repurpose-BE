"""
Publishing Rate Limiter — per-user per-platform daily post count enforcement.

Prevents users from exceeding platform posting limits (which can trigger
account bans on real platforms). Limits are sourced from the platform_catalog
collection's `posting_limits.daily` field.

Storage: `publishing_rate_limits` collection with one document per
(user_id, platform, date) triple. Documents are lightweight counters.

Usage in the publishing flow:
    from app.services.publishing_rate_limiter import check_rate_limit, record_publish

    limit_check = await check_rate_limit(user_id, "linkedin")
    if not limit_check["allowed"]:
        return {"error": f"Daily limit reached for {platform}. Resets at {limit_check['resets_at']}"}

    # ... publish ...
    await record_publish(user_id, "linkedin")
"""

from datetime import datetime, timezone, time as dt_time
from typing import Optional

from app.database import db
from app.services.logger import log

rate_limits_collection = db["publishing_rate_limits"]
platform_catalog_collection = db["platform_catalog"]

# Fallback daily limits if platform_catalog has no entry
_DEFAULT_DAILY_LIMITS: dict[str, int] = {
    "linkedin": 20,
    "instagram": 10,
    "twitter": 50,
    "reddit": 5,
    "medium": 3,
    "meta": 10,
    "quora": 10,
    "threads": 10,
}

# Cache for catalog limits (refreshed on miss)
_catalog_cache: dict[str, int] = {}
_cache_loaded = False


async def check_rate_limit(user_id: str, platform: str) -> dict:
    """
    Check if the user is allowed to publish to a platform right now.

    Returns:
        {
            "allowed": bool,
            "remaining": int,        # posts left today
            "daily_limit": int,      # total allowed per day
            "used_today": int,       # posts already made today
            "resets_at": str | None, # ISO timestamp of next midnight UTC
        }
    """
    daily_limit = await _get_daily_limit(platform)
    today = _today_str()

    doc = await rate_limits_collection.find_one({
        "user_id": user_id,
        "platform": platform,
        "date": today,
    })

    used_today = doc["count"] if doc else 0
    remaining = max(0, daily_limit - used_today)
    allowed = remaining > 0

    resets_at = _next_midnight_utc().isoformat() if not allowed else None

    return {
        "allowed": allowed,
        "remaining": remaining,
        "daily_limit": daily_limit,
        "used_today": used_today,
        "resets_at": resets_at,
    }


async def record_publish(user_id: str, platform: str) -> None:
    """
    Increment the daily publish counter for a user+platform.
    Called AFTER a successful publish (or when a post enters 'posting' state).
    Uses upsert so the first publish of the day creates the document.
    """
    today = _today_str()

    await rate_limits_collection.update_one(
        {"user_id": user_id, "platform": platform, "date": today},
        {
            "$inc": {"count": 1},
            "$setOnInsert": {
                "user_id": user_id,
                "platform": platform,
                "date": today,
                "created_at": datetime.now(timezone.utc),
            },
        },
        upsert=True,
    )


async def get_all_limits(user_id: str) -> dict:
    """
    Get rate limit status for all platforms for a user.
    Useful for dashboard display.
    """
    platforms = list(_DEFAULT_DAILY_LIMITS.keys())
    result = {}
    for platform in platforms:
        result[platform] = await check_rate_limit(user_id, platform)
    return result


async def reset_limit(user_id: str, platform: str) -> None:
    """
    Admin/debug utility: reset a user's daily counter for a platform.
    """
    today = _today_str()
    await rate_limits_collection.delete_one({
        "user_id": user_id,
        "platform": platform,
        "date": today,
    })


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _get_daily_limit(platform: str) -> int:
    """
    Get the daily posting limit for a platform.
    Reads from platform_catalog first, falls back to hardcoded defaults.
    """
    global _catalog_cache, _cache_loaded

    if not _cache_loaded:
        await _refresh_catalog_cache()

    if platform in _catalog_cache:
        return _catalog_cache[platform]

    return _DEFAULT_DAILY_LIMITS.get(platform, 10)


async def _refresh_catalog_cache() -> None:
    """Load daily limits from platform_catalog into memory."""
    global _catalog_cache, _cache_loaded
    try:
        cursor = platform_catalog_collection.find(
            {"enabled": True},
            {"platform_name": 1, "posting_limits": 1},
        )
        docs = await cursor.to_list(length=20)
        for doc in docs:
            name = doc.get("platform_name", "")
            limits = doc.get("posting_limits", {})
            daily = limits.get("daily")
            if name and daily is not None:
                _catalog_cache[name] = int(daily)
        _cache_loaded = True
    except Exception as e:
        log.warning("Failed to load platform catalog for rate limits", error=str(e))
        _cache_loaded = True  # Don't retry every call; use defaults


def _today_str() -> str:
    """Today's date as YYYY-MM-DD string (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _next_midnight_utc() -> datetime:
    """Next midnight UTC datetime."""
    from datetime import timedelta as _td
    now = datetime.now(timezone.utc)
    tomorrow = now.date() + _td(days=1)
    return datetime.combine(tomorrow, dt_time.min, tzinfo=timezone.utc)
