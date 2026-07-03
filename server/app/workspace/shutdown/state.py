"""
ShutdownGate — shared, process-wide state for a graceful complete shutdown.

Single source of truth the middleware and job entry points consult to decide
whether to admit new work while the backend is shutting down. Also tracks the
count of in-flight HTTP requests so the orchestrator can drain them.

Single event loop → plain ints are safe (no locking needed). Never raises.
"""

import asyncio
from datetime import datetime, timezone
from enum import Enum


class ShutdownPhase(str, Enum):
    IDLE = "idle"
    DRAINING = "draining"              # HTTP closed, waiting for in-flight to finish
    STOPPING_SERVICES = "stopping_services"
    POWERING_DOWN = "powering_down"
    COMPLETE = "complete"


class ShutdownGate:
    """Admission control + drain tracking for the shutdown sequence."""

    def __init__(self) -> None:
        self._http_open = True
        self._ai_open = True
        self._publishing_open = True
        self._shutting_down = False
        self._phase = ShutdownPhase.IDLE
        self._in_flight = 0
        self._started_at = None

    # ── read state ───────────────────────────────────────────────────────────
    @property
    def accepting_http(self) -> bool:
        return self._http_open

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    @property
    def phase(self) -> ShutdownPhase:
        return self._phase

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def is_accepting_ai(self) -> bool:
        """AI-generation entry points should check this and reject if False."""
        return self._ai_open

    def is_accepting_publishing(self) -> bool:
        """Publishing entry points should check this and reject if False."""
        return self._publishing_open

    # ── transitions ────────────────────────────────────────────────────────
    def enter(self) -> None:
        self._shutting_down = True
        self._started_at = datetime.now(timezone.utc)

    def set_phase(self, phase: ShutdownPhase) -> None:
        self._phase = phase

    def close_http(self) -> None:
        self._http_open = False

    def reject_ai(self) -> None:
        self._ai_open = False

    def reject_publishing(self) -> None:
        self._publishing_open = False

    # ── in-flight request accounting (used by ShutdownMiddleware) ────────────
    def inc(self) -> None:
        self._in_flight += 1

    def dec(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)

    async def drain(self, timeout_seconds: float, poll_seconds: float = 0.1) -> bool:
        """
        Wait until in-flight requests reach 0 or the timeout elapses.
        Returns True if fully drained, False if the timeout hit first.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + max(0.0, timeout_seconds)
        while self._in_flight > 0 and loop.time() < deadline:
            await asyncio.sleep(poll_seconds)
        return self._in_flight == 0

    def reset(self) -> None:
        """Restore to fully-accepting state (used only if a shutdown is aborted)."""
        self.__init__()

    def snapshot(self) -> dict:
        return {
            "shutting_down": self._shutting_down,
            "phase": self._phase.value,
            "accepting_http": self._http_open,
            "accepting_ai": self._ai_open,
            "accepting_publishing": self._publishing_open,
            "in_flight": self._in_flight,
            "started_at": self._started_at.isoformat() if self._started_at else None,
        }


# Process-wide singleton.
shutdown_gate = ShutdownGate()
