"""
Activity repository — data-access layer for user activity tracking.

Persists the "last activity" marker per user in the `user_activity` collection.
Uses an idempotent upsert (latest-wins) so writes are cheap and there is exactly
one document per user. Reused by the workspace idle logic and any future
"is the user active?" checks.

Document shape (collection: user_activity):
    {
        "_id": <user_id | "anonymous">,
        "user_id": <str | None>,
        "organization_id": <str>,
        "last_activity": <datetime UTC>,
        "last_path": <str>,
        "last_method": <str>,
        "updated_at": <datetime UTC>,
    }
"""

from datetime import datetime, timezone
from typing import Optional

from app.database import db

# Key used when a request is unauthenticated (no user_id resolvable).
ANONYMOUS_KEY = "anonymous"


class ActivityRepository:
    """MongoDB-backed store for per-user last_activity."""

    def __init__(self) -> None:
        self._collection = db["user_activity"]

    async def update_last_activity(
        self,
        user_id: Optional[str],
        organization_id: str,
        path: str,
        method: str,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """
        Upsert the caller's last_activity marker.

        Idempotent and latest-wins: a single document per user is maintained.
        Never raises into the caller — errors are logged and swallowed so
        activity tracking can never affect a request.
        """
        ts = timestamp or datetime.now(timezone.utc)
        key = user_id or ANONYMOUS_KEY
        try:
            await self._collection.update_one(
                {"_id": key},
                {
                    "$set": {
                        "user_id": user_id,
                        "organization_id": organization_id,
                        "last_activity": ts,
                        "last_path": path,
                        "last_method": method,
                        "updated_at": ts,
                    }
                },
                upsert=True,
            )
        except Exception as e:  # pragma: no cover - defensive
            print(f"[activity] last_activity upsert failed for {key}: {e}")

    async def get_last_activity(self, user_id: str) -> Optional[dict]:
        """Return the stored activity document for a user, or None."""
        try:
            doc = await self._collection.find_one({"_id": user_id or ANONYMOUS_KEY})
            if not doc:
                return None
            ts = doc.get("last_activity")
            return {
                "user_id": doc.get("user_id"),
                "organization_id": doc.get("organization_id"),
                "last_activity": ts.isoformat() if isinstance(ts, datetime) else ts,
                "last_path": doc.get("last_path"),
                "last_method": doc.get("last_method"),
            }
        except Exception:
            return None

    async def get_most_recent_activity(self) -> Optional[datetime]:
        """
        Return the single most recent `last_activity` timestamp across ALL users.

        This is the workspace-global "last API activity" signal used by the Sleep
        Decision Engine (the workspace/instance is shared, so the newest activity
        of any user keeps it awake). Returns None if there is no activity yet.
        """
        try:
            cursor = (
                self._collection.find({}, {"last_activity": 1})
                .sort("last_activity", -1)
                .limit(1)
            )
            docs = await cursor.to_list(length=1)
            if not docs:
                return None
            ts = docs[0].get("last_activity")
            return ts if isinstance(ts, datetime) else None
        except Exception:
            return None
