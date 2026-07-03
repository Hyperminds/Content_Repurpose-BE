"""
StateTransitions — the allowed lifecycle transition table.

Single source of truth for which state changes are legal. The
WorkspaceStateMachine consults this to accept or reject a transition.

    STOPPED       → STARTING
    STARTING      → RUNNING | ERROR | STOPPED (cancelled)
    RUNNING       → SHUTTING_DOWN | ERROR
    SHUTTING_DOWN → STOPPED | ERROR
    ERROR         → STARTING (retry) | STOPPED (reset)
"""

from app.workspace.lifecycle.states import WorkspaceLifecycleState as S


class StateTransitions:
    _ALLOWED = {
        S.STOPPED: {S.STARTING},
        S.STARTING: {S.RUNNING, S.ERROR, S.STOPPED},
        S.RUNNING: {S.SHUTTING_DOWN, S.ERROR},
        S.SHUTTING_DOWN: {S.STOPPED, S.ERROR},
        S.ERROR: {S.STARTING, S.STOPPED},
    }

    @classmethod
    def allowed_from(cls, state: S) -> set:
        """The set of states reachable from `state`."""
        return set(cls._ALLOWED.get(state, set()))

    @classmethod
    def is_allowed(cls, frm: S, to: S) -> bool:
        """Whether `frm → to` is a legal transition."""
        return to in cls._ALLOWED.get(frm, set())
