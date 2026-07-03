"""
Workspace Lifecycle package.

Manages the complete workspace lifecycle and coordinates the collaborators.

    WorkspaceLifecycleState — STOPPED | STARTING | RUNNING | SHUTTING_DOWN | ERROR
    StateTransitions        — the legal transition table
    WorkspaceStateMachine   — holds state, enforces transitions (pure)
    LifecycleEvents         — event types + bus (notifies frontend via WebSocket)
    WorkspaceLifecycleManager — coordinator (power, shutdown, health, activity)
"""

from app.workspace.lifecycle.states import WorkspaceLifecycleState
from app.workspace.lifecycle.transitions import StateTransitions
from app.workspace.lifecycle.state_machine import WorkspaceStateMachine, InvalidTransitionError
from app.workspace.lifecycle.events import (
    LifecycleEvent,
    LifecycleEventType,
    LifecycleEventBus,
)
from app.workspace.lifecycle.manager import WorkspaceLifecycleManager, get_lifecycle_manager

__all__ = [
    "WorkspaceLifecycleState",
    "StateTransitions",
    "WorkspaceStateMachine",
    "InvalidTransitionError",
    "LifecycleEvent",
    "LifecycleEventType",
    "LifecycleEventBus",
    "WorkspaceLifecycleManager",
    "get_lifecycle_manager",
]
