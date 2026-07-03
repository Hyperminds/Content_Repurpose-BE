"""
WorkspaceLifecycleState — the canonical lifecycle states.

Distinct from PowerState (a coarse infra view) and the frontend WORKSPACE_STATES
UI enum: this is the backend's authoritative lifecycle model.
"""

from enum import Enum


class WorkspaceLifecycleState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    ERROR = "ERROR"
