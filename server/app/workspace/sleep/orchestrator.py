"""
SleepOrchestrator — coordinates every step required before the workspace sleeps.

Sequence (per spec):
    1. Validate the SleepDecision      (abort if not allowed to sleep)
    2. Flush pending metrics
    3. Save latest activity timestamp
    4. Close WebSocket sessions gracefully
    5. Stop background scheduler
    6. Persist workspace state
    7. Log shutdown event
    8. Finally call PowerController.shutdown()

Design notes
------------
• The orchestrator DECIDES + SEQUENCES; it delegates each concrete action to an
  injected SleepHooks and the terminal action to an injected PowerController.
  This keeps it testable (pass fakes) and swappable (real EC2 controller later).
• Steps 2–6 are best-effort: a single failure is recorded as a STEP_FAILED event
  but does not abort the shutdown (we still want to release resources and sleep).
  Step 1 is a hard gate — if the decision says "don't sleep", nothing runs.
• Every step emits a SleepEvent, producing an ordered, structured trace returned
  in the report and written to the log.
• No AWS: PowerController.shutdown() is a no-op stub by default.
"""

from typing import Optional

from app.services.logger import log
from app.workspace.sleep.decision import SleepDecision
from app.workspace.sleep.engine import SleepDecisionEngine, get_sleep_engine
from app.workspace.sleep.events import SleepEvent, SleepEventLog
from app.workspace.sleep.hooks import SleepHooks
from app.workspace.sleep.power_controller import PowerController, get_power_controller


class SleepOrchestrator:
    """Runs the ordered pre-sleep sequence and finally triggers power sleep."""

    def __init__(
        self,
        hooks: Optional[SleepHooks] = None,
        power_controller: Optional[PowerController] = None,
        engine: Optional[SleepDecisionEngine] = None,
    ) -> None:
        self._hooks = hooks or SleepHooks()
        self._power = power_controller or get_power_controller()
        self._engine = engine or get_sleep_engine()

    async def sleep(self, decision: Optional[SleepDecision] = None) -> dict:
        """
        Execute the full sleep sequence.

        `decision` may be supplied (e.g. from a scheduler that already evaluated)
        or, if omitted, the orchestrator evaluates it via the SleepDecisionEngine.
        Returns a structured report: the final decision, an ordered event trace,
        and the PowerController result.
        """
        events = SleepEventLog()
        events.emit(SleepEvent.SLEEP_REQUESTED)

        # ── 1. Validate the SleepDecision (hard gate) ──────────────────────
        if decision is None:
            decision = await self._engine.evaluate()

        if not decision.should_sleep:
            events.emit(SleepEvent.SLEEP_ABORTED, ok=True, detail=decision.reason)
            log.info(f"[sleep] aborted — {decision.reason}")
            return self._report(decision, events, powered=None, completed=False)

        events.emit(SleepEvent.DECISION_VALIDATED, detail=decision.reason)

        try:
            # ── 2–6. Best-effort resource teardown ─────────────────────────
            await self._run_step(events, SleepEvent.METRICS_FLUSHED, self._hooks.flush_metrics)
            await self._run_step(events, SleepEvent.ACTIVITY_SAVED, self._hooks.save_activity)
            await self._run_step(events, SleepEvent.WEBSOCKETS_CLOSED, self._hooks.close_websockets)
            await self._run_step(events, SleepEvent.SCHEDULER_STOPPED, self._hooks.stop_scheduler)
            await self._run_step(events, SleepEvent.STATE_PERSISTED, self._hooks.persist_state)

            # ── 7. Log shutdown event ──────────────────────────────────────
            log.info(f"[sleep] shutdown sequence complete — {decision.reason}")
            events.emit(SleepEvent.SHUTDOWN_LOGGED)

            # ── 8. Finally, power down (no AWS yet) ─────────────────────────
            power_result = await self._power.shutdown()
            events.emit(SleepEvent.POWER_SLEEP_INVOKED, ok=True, detail=power_result)

            events.emit(SleepEvent.SLEEP_COMPLETED)
            return self._report(decision, events, powered=power_result, completed=True)

        except Exception as e:  # pragma: no cover - defensive
            log.error(f"[sleep] orchestration failed: {e}")
            events.emit(SleepEvent.SLEEP_FAILED, ok=False, detail=str(e))
            return self._report(decision, events, powered=None, completed=False)

    # ── helpers ────────────────────────────────────────────────────────────
    async def _run_step(self, events: SleepEventLog, event: SleepEvent, fn) -> None:
        """Run one best-effort hook, emitting success or STEP_FAILED."""
        try:
            result = await fn()
            ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
            if ok:
                events.emit(event, ok=True, detail=result)
            else:
                events.emit(SleepEvent.STEP_FAILED, ok=False, detail={"step": event.value, **result})
                log.error(f"[sleep] step failed: {event.value} — {result}")
        except Exception as e:
            events.emit(SleepEvent.STEP_FAILED, ok=False, detail={"step": event.value, "error": str(e)})
            log.error(f"[sleep] step raised: {event.value} — {e}")

    def _report(self, decision: SleepDecision, events: SleepEventLog, powered, completed: bool) -> dict:
        return {
            "completed": completed,
            "decision": decision.to_dict(),
            "power": powered,
            "events": events.to_list(),
        }


def get_sleep_orchestrator() -> SleepOrchestrator:
    """Accessor / DI provider for a SleepOrchestrator (wired to real subsystems)."""
    return SleepOrchestrator()
