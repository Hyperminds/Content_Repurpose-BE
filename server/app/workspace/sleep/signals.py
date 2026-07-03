"""
Workspace activity signals for the Sleep Decision Engine.

Two pieces live here:

  • WorkspaceActivityRegistry — a tiny, thread/async-safe in-memory counter store
    for work that has no external counter yet (AI generations, publishing jobs,
    pending uploads). It is the INTEGRATION SEAM: subsystems wrap their work in
    `registry.track_ai()` / `.track_publishing()` / `.track_upload()` (or call
    begin/end) so the engine can see them. Nothing here modifies existing
    business logic — integration is opt-in and additive.

  • SignalCollector — gathers a point-in-time WorkspaceActivitySignals snapshot
    from all sources (WebSocket manager, background task queue, the registry, and
    the persisted last_activity). Every read is fail-safe.
"""

from contextlib import contextmanager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional


@dataclass
class WorkspaceActivitySignals:
    """Immutable-ish snapshot of everything the validator reasons about."""

    last_activity: Optional[datetime] = None
    seconds_since_last_activity: Optional[float] = None
    active_ws_connections: int = 0
    running_ai_generations: int = 0
    active_publishing_jobs: int = 0
    active_background_tasks: int = 0
    pending_uploads: int = 0

    def as_dict(self) -> dict:
        return {
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "seconds_since_last_activity": self.seconds_since_last_activity,
            "active_ws_connections": self.active_ws_connections,
            "running_ai_generations": self.running_ai_generations,
            "active_publishing_jobs": self.active_publishing_jobs,
            "active_background_tasks": self.active_background_tasks,
            "pending_uploads": self.pending_uploads,
        }


class WorkspaceActivityRegistry:
    """
    In-memory counters for in-flight work that has no other live signal.

    Safe defaults (0). Never raises. Designed so a subsystem can do:

        async with activity_registry.track_ai():
            ... run the AI generation ...

    and the counter is guaranteed to decrement even on error.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._ai = 0
        self._publishing = 0
        self._uploads = 0

    # ── low-level begin/end ────────────────────────────────────────────────
    def _inc(self, attr: str) -> None:
        with self._lock:
            setattr(self, attr, getattr(self, attr) + 1)

    def _dec(self, attr: str) -> None:
        with self._lock:
            setattr(self, attr, max(0, getattr(self, attr) - 1))

    def begin_ai(self) -> None: self._inc("_ai")
    def end_ai(self) -> None: self._dec("_ai")
    def begin_publishing(self) -> None: self._inc("_publishing")
    def end_publishing(self) -> None: self._dec("_publishing")
    def begin_upload(self) -> None: self._inc("_uploads")
    def end_upload(self) -> None: self._dec("_uploads")

    # ── ergonomic context managers (sync + async) ──────────────────────────
    @contextmanager
    def track_ai(self):
        self.begin_ai()
        try:
            yield
        finally:
            self.end_ai()

    @asynccontextmanager
    async def track_ai_async(self):
        self.begin_ai()
        try:
            yield
        finally:
            self.end_ai()

    @contextmanager
    def track_publishing(self):
        self.begin_publishing()
        try:
            yield
        finally:
            self.end_publishing()

    @contextmanager
    def track_upload(self):
        self.begin_upload()
        try:
            yield
        finally:
            self.end_upload()

    # ── snapshot ───────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running_ai_generations": self._ai,
                "active_publishing_jobs": self._publishing,
                "pending_uploads": self._uploads,
            }


# Process-wide registry singleton.
activity_registry = WorkspaceActivityRegistry()


class SignalCollector:
    """Assembles a WorkspaceActivitySignals snapshot from all live sources."""

    def __init__(self, registry: WorkspaceActivityRegistry = activity_registry) -> None:
        self._registry = registry

    def _ws_connections(self) -> int:
        try:
            from app.ws.manager import ws_manager
            return int(ws_manager.active_connections)
        except Exception:
            return 0

    def _background_tasks(self) -> int:
        try:
            from app.services.background_tasks import task_queue
            stats = task_queue.get_stats()
            # "Active" = currently running + still queued.
            return int(stats.get("running", 0)) + int(stats.get("queued", 0))
        except Exception:
            return 0

    async def _most_recent_activity(self) -> Optional[datetime]:
        try:
            from app.repositories.activity_repository import ActivityRepository
            return await ActivityRepository().get_most_recent_activity()
        except Exception:
            return None

    async def collect(self) -> WorkspaceActivitySignals:
        """Gather every signal. Fail-safe — missing sources degrade to 0/None."""
        reg = self._registry.snapshot()

        last_activity = await self._most_recent_activity()
        seconds_since = None
        if last_activity is not None:
            now = datetime.now(timezone.utc)
            # Persisted timestamps may be naive UTC; normalize before diffing.
            if last_activity.tzinfo is None:
                last_activity = last_activity.replace(tzinfo=timezone.utc)
            seconds_since = max(0.0, (now - last_activity).total_seconds())

        return WorkspaceActivitySignals(
            last_activity=last_activity,
            seconds_since_last_activity=seconds_since,
            active_ws_connections=self._ws_connections(),
            running_ai_generations=reg["running_ai_generations"],
            active_publishing_jobs=reg["active_publishing_jobs"],
            active_background_tasks=self._background_tasks(),
            pending_uploads=reg["pending_uploads"],
        )
