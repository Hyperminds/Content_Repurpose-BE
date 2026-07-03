"""
SleepHooks — the concrete shutdown steps the orchestrator runs.

Each hook is:
  • Single-responsibility (one of the pre-sleep steps).
  • Async and fail-safe — it returns a small result dict and never raises, so one
    failing step can't crash the sequence (the orchestrator decides how to react).
  • Wired to the real subsystems (metering worker, activity repo, ws manager,
    scheduler, workspace state) but injectable, so tests can pass fakes.

Business logic is untouched — hooks only call existing shutdown/flush entry
points that the app already exposes.
"""

from datetime import datetime, timezone
from typing import Optional


class SleepHooks:
    """Collection of the pre-sleep steps, in no particular order (the
    orchestrator sequences them)."""

    async def flush_metrics(self) -> dict:
        """Drain + flush the in-memory metering queue to MongoDB."""
        try:
            from app.services.metering_service import stop_metering_worker, get_worker_stats
            pending = 0
            try:
                pending = get_worker_stats().get("queued", 0)
            except Exception:
                pass
            await stop_metering_worker()
            return {"ok": True, "flushed_pending": pending}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def save_activity(self) -> dict:
        """Persist the workspace's final activity timestamp."""
        try:
            from app.repositories.activity_repository import ActivityRepository
            ts = datetime.now(timezone.utc)
            await ActivityRepository().update_last_activity(
                user_id="__workspace__",
                organization_id="system",
                path="/workspace/sleep",
                method="SYSTEM",
                timestamp=ts,
            )
            return {"ok": True, "saved_at": ts.isoformat()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def close_websockets(self) -> dict:
        """Gracefully close all active WebSocket sessions."""
        try:
            from app.ws.manager import ws_manager
            closed = await ws_manager.close_all(reason="Workspace entering sleep")
            return {"ok": True, "closed_connections": closed}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def stop_scheduler(self) -> dict:
        """Stop the background publishing scheduler + trigger consumer."""
        try:
            from app.services.scheduler_worker import stop_scheduler
            stop_scheduler()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def persist_state(self, state: str = "sleeping") -> dict:
        """
        Persist the workspace lifecycle state so it survives the shutdown and can
        be restored/inspected on next boot. Writes a single workspace_state doc
        and updates the in-memory WorkspaceService if available.
        """
        try:
            from app.database import db
            ts = datetime.now(timezone.utc)
            await db["workspace_state"].update_one(
                {"_id": "singleton"},
                {"$set": {"state": state, "updated_at": ts}},
                upsert=True,
            )
            # Best-effort: reflect the persisted state in the live service.
            try:
                from app.workspace.workspace_service import get_workspace_service
                from app.workspace.workspace_models import WorkspaceState
                get_workspace_service().get_status().state = WorkspaceState(state)
            except Exception:
                pass
            return {"ok": True, "state": state, "persisted_at": ts.isoformat()}
        except Exception as e:
            return {"ok": False, "error": str(e)}
