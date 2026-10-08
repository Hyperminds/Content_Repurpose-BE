"""Per-platform rate-limit tracking — prevents exceeding platform posting limits.

Maintains daily counters per (tenant_id, platform) pair in MongoDB.
The scheduler checks this BEFORE enqueueing a job. If the limit is reached,
the job is not created and the post stays in SCHEDULED state for the next day.

Limits are conservative defaults (not the actual platform max) to avoid
triggering platform-side enforcement which could disable accounts.
"""

from datetime import datetime, timezone, time as dt_time, timedelta
from typing import Optional

from app.database import db
from app.social_publishing.domain.enums import SocialPlatform

_collection = db["sp_rate_limits"]

# Conservative daily limits per platform (well below platform maximums)
_DAILY_LIMITS: dict[str, int] = {
    "linkedin": 20,
    "instagram": 25,
    "meta": 25,
    "twitter": 50,
    "threads": 25,
    "reddit": 5,
    "medium": 3,
    "quora": 10,
}


class PublishingRateLimiter:
    """Tracks and enforces per-tenant per-platform daily publishing limits."""

    async def check(self, tenant_id: str, platform: str) -> dict:
        """
        Check if a tenant can publish to a platform right now.

        Returns:
            {
                "allowed": bool,
                "remaining": int,
                "daily_limit": int,
                "used_today": int,
                "resets_at": str (ISO) or None,
            }
        """
        daily_limit = _DAILY_LIMITS.get(platform, 10)
        today = _today_key()

        doc = await _collection.find_one({
            "tenant_id": tenant_id,
            "platform": platform,
            "date": today,
        })

        used = doc["count"] if doc else 0
        remaining = max(0, daily_limit - used)
        allowed = remaining > 0

        return {
            "allowed": allowed,
            "remaining": remaining,
            "daily_limit": daily_limit,
            "used_today": used,
            "resets_at": _next_midnight().isoformat() if not allowed else None,
        }

    async def increment(self, tenant_id: str, platform: str) -> None:
        """
        Record a publish action. Called AFTER a job is successfully enqueued.
        Uses upsert so first action of the day creates the document.
        """
        today = _today_key()
        await _collection.update_one(
            {"tenant_id": tenant_id, "platform": platform, "date": today},
            {
                "$inc": {"count": 1},
                "$setOnInsert": {
                    "tenant_id": tenant_id,
                    "platform": platform,
                    "date": today,
                    "created_at": datetime.now(timezone.utc),
                },
            },
            upsert=True,
        )

    async def get_all_limits(self, tenant_id: str) -> dict[str, dict]:
        """Get rate limit status for all platforms for a tenant."""
        result: dict[str, dict] = {}
        for platform in _DAILY_LIMITS:
            result[platform] = await self.check(tenant_id, platform)
        return result

    async def reset(self, tenant_id: str, platform: str) -> None:
        """Admin/debug: reset a tenant's daily counter."""
        today = _today_key()
        await _collection.delete_one({
            "tenant_id": tenant_id,
            "platform": platform,
            "date": today,
        })


def _today_key() -> str:
    """Today's date as YYYY-MM-DD (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _next_midnight() -> datetime:
    """Next midnight UTC."""
    now = datetime.now(timezone.utc)
    tomorrow = now.date() + timedelta(days=1)
    return datetime.combine(tomorrow, dt_time.min, tzinfo=timezone.utc)
