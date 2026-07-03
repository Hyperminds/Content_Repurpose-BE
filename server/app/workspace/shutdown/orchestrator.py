"""
ShutdownOrchestrator — coordinates a COMPLETE backend shutdown.

This is a full stop (NOT sleep/hibernate): after this sequence the process has
released its resources so the instance can safely be stopped externally.

Sequence (per spec):
    1.  Stop accepting new HTTP requests
    2.  Allow existing requests to finish (drain)
    3.  Reject new AI generation jobs
    4.  Reject new publishing jobs
    5.  Gracefully disconnect WebSockets
    6.  Stop background scheduler
    7.  Flush logs
    8.  Flush metrics
    9.  Persist last activity timestamp
    10. Save workspace state
    11. Trigger PowerController.shutdown()   (logs only — no EC2/boto3 yet)

Design:
  • Validates the ShutdownDecision as a hard gate (aborts if not allowed).
  • Steps 1–10 are best-effort — a failing step is recorded but does not abort
    the sequence (we still want to release resources and request power-down).
  • Delegates every action to injected ShutdownHooks + PowerController, so it is
    testable (fakes) and swappable (real power controller later).
  • Drives the ShutdownGate phases for observability.
"""

from typing import Optional

from app.services.logger import log
from app.workspace.power.base import PowerController
from app.workspace.power.factory import get_power_controller
from app.workspace.shutdown.decision import ShutdownDecision
from app.workspace.shutdown.engine import ShutdownDecisionEngine, get_shutdown_engine
from app.workspace.shutdown.hooks import ShutdownHooks
from app.workspace.shutdown.state import ShutdownPhase, shutdown_gate


class ShutdownOrchestrator:
    def __init__(
        self,
        hooks: Optional[ShutdownHooks] = None,
        power_controller: Optional[PowerController] = None,
        engine: Optional[ShutdownDecisionEngine] = None,
        gate=shutdown_gate,
    ) -> None:
        self._hooks = hooks or ShutdownHooks()
        self._power = power_controller or get_power_controller()
        self._engine = engine or get_shutdown_engine()
        self._gate = gate

    async def shutdown(self, decision: Optional[ShutdownDecision] = None) -> dict:
        steps: list[dict] = []

        # ── Validate (hard gate) ───────────────────────────────────────────
        if decision is None:
            decision = await self._engine.evaluate()
        if not decision.allowed:
            log.info(f"[shutdown] aborted — {decision.reason}")
            return {"completed": False, "aborted": True, "decision": decision.to_dict(), "steps": steps}

        self._gate.enter()
        log.info(f"[shutdown] starting complete shutdown — {decision.reason}")

        try:
            # 1–4: stop admitting new work, drain existing
            self._gate.set_phase(ShutdownPhase.DRAINING)
            await self._run(steps, "stop_accepting_requests", self._hooks.stop_accepting_requests)
            await self._run(steps, "drain_requests", self._hooks.drain_requests)
            await self._run(steps, "reject_ai_jobs", self._hooks.reject_ai_jobs)
            await self._run(steps, "reject_publishing_jobs", self._hooks.reject_publishing_jobs)

            # 5–10: tear down services + persist
            self._gate.set_phase(ShutdownPhase.STOPPING_SERVICES)
            await self._run(steps, "close_websockets", self._hooks.close_websockets)
            await self._run(steps, "stop_scheduler", self._hooks.stop_scheduler)
            await self._run(steps, "flush_logs", self._hooks.flush_logs)
            await self._run(steps, "flush_metrics", self._hooks.flush_metrics)
            await self._run(steps, "persist_last_activity", self._hooks.save_activity)
            await self._run(steps, "save_workspace_state", self._hooks.persist_state)

            # 11: request power-down (logs only for now)
            self._gate.set_phase(ShutdownPhase.POWERING_DOWN)
            power_result = await self._power.shutdown()
            steps.append({"step": "power_shutdown", "ok": True, "result": power_result})

            self._gate.set_phase(ShutdownPhase.COMPLETE)
            log.info("[shutdown] complete — PowerController.shutdown() requested")
            return {"completed": True, "decision": decision.to_dict(), "steps": steps, "power": power_result}

        except Exception as e:  # pragma: no cover - defensive
            log.error(f"[shutdown] orchestration failed: {e}")
            steps.append({"step": "orchestration", "ok": False, "error": str(e)})
            return {"completed": False, "decision": decision.to_dict(), "steps": steps}

    async def _run(self, steps: list, name: str, fn) -> None:
        """Run one best-effort step, recording its result (never raises)."""
        try:
            result = await fn()
            ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
            steps.append({"step": name, "ok": ok, "detail": result})
            if not ok:
                log.error(f"[shutdown] step reported failure: {name} — {result}")
        except Exception as e:
            steps.append({"step": name, "ok": False, "error": str(e)})
            log.error(f"[shutdown] step raised: {name} — {e}")


def get_shutdown_orchestrator() -> ShutdownOrchestrator:
    """Accessor / DI provider for a ShutdownOrchestrator wired to real subsystems."""
    return ShutdownOrchestrator()
