"""
Activity service — orchestrates last_activity tracking.

Sits between the ActivityMiddleware and the ActivityRepository. Its one job is
to record activity WITHOUT adding latency to the request path: the actual DB
upsert is offloaded to a fire-and-forget asyncio task, so the middleware returns
the response immediately and the write happens after.

Fail-safe: every failure mode (no event loop, DB error, etc.) is swallowed.
Activity tracking must never affect an API response.
"""

import asyncio
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from app.repositories.activity_repository import ActivityRepository


class ActivityService:
    """Non-blocking recorder for per-user last_activity."""

    def __init__(self, repository: Optional[ActivityRepository] = None) -> None:
        self._repo = repository or ActivityRepository()

    def track(
        self,
        user_id: Optional[str],
        organization_id: str,
        path: str,
        method: str,
    ) -> None:
        """
        Schedule a last_activity update and return immediately.

        Called from the request path — MUST be non-blocking and MUST NOT raise.
        The timestamp is captured now (at request time), but the write is
        deferred to a background task so it never adds to request latency.
        """
        ts = datetime.now(timezone.utc)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self._repo.update_last_activity(user_id, organization_id, path, method, ts)
            )
        except RuntimeError:
            # No running loop (shouldn't happen inside async middleware) — skip.
            pass
        except Exception:
            # Never let activity tracking break the request.
            pass

    async def get_last_activity(self, user_id: str) -> Optional[dict]:
        """Read the stored last_activity for a user (used by status endpoints)."""
        return await self._repo.get_last_activity(user_id)


@lru_cache(maxsize=1)
def _service_singleton() -> ActivityService:
    """Process-wide ActivityService (shares one repository/collection handle)."""
    return ActivityService()


def get_activity_service() -> ActivityService:
    """FastAPI dependency provider / accessor for the shared ActivityService."""
    return _service_singleton()
