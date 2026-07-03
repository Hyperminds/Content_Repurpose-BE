"""
Workspace domain models.

Internal (server-side) representation of the workspace power lifecycle.
These are the domain types the service reasons about — distinct from the
API-facing Pydantic schemas in workspace_schemas.py.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class WorkspaceState(str, Enum):
    """
    Canonical workspace power states.

    Values are lowercase to match the public API contract
    (e.g. GET /workspace/status → {"status": "sleeping"}).
    Mirrors the frontend WORKSPACE_STATES.
    """

    SLEEPING = "sleeping"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class WorkspaceSnapshot:
    """
    Point-in-time view of the workspace held by the service.

    In-memory only for now — this is the swap point for real infrastructure
    state (e.g. an EC2 instance status or a persisted record) later.
    """

    state: WorkspaceState = WorkspaceState.SLEEPING
    # Estimated cold-start duration surfaced to clients (seconds).
    estimated_startup: int = 30
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        """Record activity (called on any state transition)."""
        self.last_activity = datetime.now(timezone.utc)
