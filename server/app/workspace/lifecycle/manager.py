"""
WorkspaceLifecycleManager — the single coordinator for the workspace lifecycle.

Responsibilities:
  • Track workspace state via the WorkspaceStateMachine (STOPPED → STARTING →
    RUNNING → SHUTTING_DOWN → STOPPED, with ERROR as the failure state).
  • Notify the frontend on every transition via the LifecycleEventBus (WebSocket).
  • Coordinate the collaborators, each injected (Dependency Inversion):
        - PowerController      → startup() / status()
        - ShutdownOrchestrator → the full teardown sequence for shutdown()
        - Health services      → confirm readiness before declaring RUNNING
        - Activity tracking    → record lifecycle activity

Concurrency: a single asyncio lock serializes start()/shutdown() so the machine
can never process two lifecycle operations at once.

Note: shutdown() runs the real ShutdownOrchestrator (which tears services down);
it is a deliberate, explicit operation — nothing here auto-triggers it.
"""

import asyncio
from typing import Callable, Optional

from app.services.logger import log
from app.services.activity_service import get_activity_service
from app.workspace.power.factory import get_power_controller
from app.workspace.shutdown.decision import ShutdownDecision
from app.workspace.shutdown.engine import get_shutdown_engine
from app.workspace.shutdown.orchestrator import get_shutdown_orchestrator
from app.workspace.lifecycle.events import LifecycleEvent, LifecycleEventBus, LifecycleEventType
from app.workspace.lifecycle.state_machine import WorkspaceStateMachine
from app.workspace.lifecycle.states import WorkspaceLifecycleState as S


async def _default_health_check() -> bool:
    """Readiness probe used to confirm RUNNING — pings MongoDB."""
    try:
        from app.database import client
        await client.admin.command("ping")
        return True
    except Exception:
        return False


class WorkspaceLifecycleManager:
    def __init__(
        self,
        machine: Optional[WorkspaceStateMachine] = None,
        events: Optional[LifecycleEventBus] = None,
        power_controller=None,
        shutdown_orchestrator=None,
        shutdown_engine=None,
        activity_service=None,
        health_check: Optional[Callable] = None,
    ) -> None:
        self._machine = machine or WorkspaceStateMachine()
        self._events = events or LifecycleEventBus()
        self._power = power_controller or get_power_controller()
        self._shutdown = shutdown_orchestrator or get_shutdown_orchestrator()
        self._shutdown_engine = shutdown_engine or get_shutdown_engine()
        self._activity = activity_service or get_activity_service()
        self._health_check = health_check or _default_health_check
        self._error: Optional[str] = None
        self._lock = asyncio.Lock()

    # ── observation ──────────────────────────────────────────────────────────
    @property
    def state(self) -> S:
        return self._machine.state

    def subscribe(self, callback):
        """Register a lifecycle listener (returns an unsubscribe fn)."""
        return self._events.subscribe(callback)

    def snapshot(self) -> dict:
        snap = self._machine.snapshot()
        snap["provider"] = getattr(self._power, "name", "unknown")
        snap["error"] = self._error
        return snap

    # ── operations ───────────────────────────────────────────────────────────
    async def start(self, reason: str = "manual") -> dict:
        """
        Bring the workspace up: STOPPED/ERROR → STARTING → RUNNING (or ERROR).
        Idempotent: no-op if already RUNNING/STARTING.
        """
        async with self._lock:
            if self._machine.state in (S.RUNNING, S.STARTING):
                return self.snapshot()
            if not self._machine.can_transition(S.STARTING):
                return {**self.snapshot(), "rejected": f"cannot start from {self._machine.state.value}"}

            self._error = None
            await self._go(S.STARTING, LifecycleEventType.STARTUP_REQUESTED, reason)

            try:
                result = await self._power.startup()
                if isinstance(result, dict) and not result.get("ok", True):
                    raise RuntimeError(result.get("detail", "power startup failed"))

                healthy = await self._health_check()
                if not healthy:
                    raise RuntimeError("health check did not pass after startup")

                await self._go(S.RUNNING, LifecycleEventType.STARTUP_COMPLETED, "healthy")
                # Record lifecycle activity (fire-and-forget, non-blocking).
                self._activity.track(
                    user_id="__workspace__", organization_id="system",
                    path="/workspace/lifecycle/start", method="SYSTEM",
                )
            except Exception as e:
                self._error = str(e)
                await self._go(S.ERROR, LifecycleEventType.ERROR, str(e))
            return self.snapshot()

    async def shutdown(self, reason: str = "idle", force: bool = False) -> dict:
        """
        Take the workspace down: RUNNING → SHUTTING_DOWN → STOPPED (or ERROR).

        Unless `force`, the ShutdownDecisionEngine must permit it (idle + no
        active work); otherwise the workspace stays RUNNING and the reason is
        returned. Delegates the teardown to the ShutdownOrchestrator.
        """
        async with self._lock:
            if self._machine.state in (S.STOPPED, S.SHUTTING_DOWN):
                return self.snapshot()
            if not self._machine.can_transition(S.SHUTTING_DOWN):
                return {**self.snapshot(), "rejected": f"cannot shut down from {self._machine.state.value}"}

            decision = await self._shutdown_engine.evaluate()
            if not force and not decision.allowed:
                return {**self.snapshot(), "aborted": True, "reason": decision.reason}

            self._error = None
            await self._go(S.SHUTTING_DOWN, LifecycleEventType.SHUTDOWN_REQUESTED, reason)

            try:
                effective = decision if decision.allowed else ShutdownDecision.allow(f"forced: {reason}")
                report = await self._shutdown.shutdown(effective)
                if not report.get("completed"):
                    raise RuntimeError(f"shutdown sequence incomplete: {report.get('error', 'unknown')}")
                await self._go(S.STOPPED, LifecycleEventType.SHUTDOWN_COMPLETED, "complete")
            except Exception as e:
                self._error = str(e)
                await self._go(S.ERROR, LifecycleEventType.ERROR, str(e))
            return self.snapshot()

    async def mark_running(self, reason: str = "health_confirmed") -> dict:
        """
        Externally confirm readiness (e.g. the frontend health poll observed a
        healthy backend) and move STARTING → RUNNING.
        """
        async with self._lock:
            if self._machine.state == S.STARTING and self._machine.can_transition(S.RUNNING):
                await self._go(S.RUNNING, LifecycleEventType.STARTUP_COMPLETED, reason)
            return self.snapshot()

    async def mark_error(self, reason: str) -> dict:
        """Force the ERROR state (e.g. an unrecoverable health loss)."""
        async with self._lock:
            self._error = reason
            if self._machine.can_transition(S.ERROR):
                await self._go(S.ERROR, LifecycleEventType.ERROR, reason)
            return self.snapshot()

    # ── internal ─────────────────────────────────────────────────────────────
    async def _go(self, to: S, event_type: LifecycleEventType, reason: str) -> None:
        """Transition the machine and emit the corresponding lifecycle event."""
        previous = self._machine.transition(to, reason)
        log.info(f"[lifecycle] {previous.value} → {to.value} ({reason})")
        await self._events.emit(
            LifecycleEvent(
                type=event_type,
                state=to.value,
                previous_state=previous.value,
                reason=reason,
            )
        )


_manager: Optional[WorkspaceLifecycleManager] = None


def get_lifecycle_manager() -> WorkspaceLifecycleManager:
    """Process-wide WorkspaceLifecycleManager (DI provider / accessor)."""
    global _manager
    if _manager is None:
        _manager = WorkspaceLifecycleManager()
    return _manager
