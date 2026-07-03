"""
Workspace service — business logic for the workspace power lifecycle.

Holds an in-memory state machine (no AWS, no DB yet). This class is the single
seam for real infrastructure control later: swap the bodies of `start` / `stop`
/ `health` to call EC2/Lambda/EventBridge while keeping the same return shapes.

State machine:
    SLEEPING ──start──▶ STARTING          (client then polls status)
    RUNNING  ──stop───▶ STOPPING
Idempotent: starting an already-starting/running workspace, or stopping an
already-stopping/sleeping one, returns the current state without error.

Exposed via FastAPI dependency injection (`get_workspace_service`) as a
process-wide singleton.
"""

from functools import lru_cache

from app.workspace.workspace_models import WorkspaceState, WorkspaceSnapshot


class WorkspaceService:
    """Encapsulates workspace state transitions. Transport-agnostic."""

    def __init__(self, estimated_startup: int = 30) -> None:
        self._snapshot = WorkspaceSnapshot(estimated_startup=estimated_startup)

    # ── Queries ──────────────────────────────────────────────────────────────
    def get_status(self) -> WorkspaceSnapshot:
        """Return the current workspace snapshot."""
        return self._snapshot

    def health(self) -> dict:
        """Liveness of the workspace module itself (always healthy in-process)."""
        return {"status": "healthy"}

    # ── Transitions ──────────────────────────────────────────────────────────
    def start(self) -> WorkspaceState:
        """
        Begin waking the workspace.

        SLEEPING | ERROR ──▶ STARTING. If already STARTING/RUNNING, returns the
        current state unchanged (idempotent). Real cold-start would kick off here
        (e.g. EC2 start-instances) and a subsequent status poll would flip to
        RUNNING once healthy.
        """
        if self._snapshot.state in (WorkspaceState.SLEEPING, WorkspaceState.ERROR):
            self._snapshot.state = WorkspaceState.STARTING
            self._snapshot.touch()
        return self._snapshot.state

    def stop(self) -> WorkspaceState:
        """
        Begin pausing the workspace.

        RUNNING ──▶ STOPPING. If already STOPPING/SLEEPING, returns the current
        state unchanged (idempotent).
        """
        if self._snapshot.state == WorkspaceState.RUNNING:
            self._snapshot.state = WorkspaceState.STOPPING
            self._snapshot.touch()
        return self._snapshot.state


@lru_cache(maxsize=1)
def _service_singleton() -> WorkspaceService:
    """Create the process-wide WorkspaceService once."""
    return WorkspaceService()


def get_workspace_service() -> WorkspaceService:
    """
    FastAPI dependency provider.

    Yields the singleton WorkspaceService so all requests share the same
    in-memory state. Injected into routes via `Depends(get_workspace_service)`.
    """
    return _service_singleton()
