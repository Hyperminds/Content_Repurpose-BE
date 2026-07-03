"""
WorkspaceStateMachine — holds the current lifecycle state and enforces the
StateTransitions rules. Pure, synchronous, side-effect-free (no I/O, no events);
the WorkspaceLifecycleManager owns the side effects. This keeps the machine
trivially testable.
"""

from datetime import datetime, timezone
from typing import List

from app.workspace.lifecycle.states import WorkspaceLifecycleState
from app.workspace.lifecycle.transitions import StateTransitions


class InvalidTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""


class WorkspaceStateMachine:
    def __init__(self, initial: WorkspaceLifecycleState = WorkspaceLifecycleState.STOPPED) -> None:
        self._state = initial
        self._changed_at = datetime.now(timezone.utc)
        self._history: List[dict] = []

    @property
    def state(self) -> WorkspaceLifecycleState:
        return self._state

    @property
    def changed_at(self) -> datetime:
        return self._changed_at

    def can_transition(self, to: WorkspaceLifecycleState) -> bool:
        return StateTransitions.is_allowed(self._state, to)

    def transition(self, to: WorkspaceLifecycleState, reason: str = "") -> WorkspaceLifecycleState:
        """
        Apply `state → to`, returning the previous state.
        Raises InvalidTransitionError if the transition is not permitted.
        """
        if not self.can_transition(to):
            raise InvalidTransitionError(
                f"Illegal transition {self._state.value} → {to.value}"
            )
        previous = self._state
        self._state = to
        self._changed_at = datetime.now(timezone.utc)
        self._history.append(
            {
                "from": previous.value,
                "to": to.value,
                "reason": reason,
                "at": self._changed_at.isoformat(),
            }
        )
        # Keep history bounded.
        if len(self._history) > 50:
            self._history.pop(0)
        return previous

    def snapshot(self) -> dict:
        return {
            "state": self._state.value,
            "since": self._changed_at.isoformat(),
            "allowed_next": sorted(s.value for s in StateTransitions.allowed_from(self._state)),
            "recent_transitions": self._history[-10:],
        }
