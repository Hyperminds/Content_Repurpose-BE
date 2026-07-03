"""
ShutdownDecisionEngine — decides whether a complete backend shutdown is allowed.

The question "is it safe to go down?" is identical to "is it safe to sleep?", so
this delegates to the existing SleepDecisionEngine (single source of truth for
the activity/idle criteria) and maps its result to a ShutdownDecision. It also
short-circuits if a shutdown is already in progress.

Fail-safe: on any unexpected error it BLOCKS (never shut down on uncertainty).
"""

from functools import lru_cache
from typing import Optional

from app.workspace.sleep.engine import SleepDecisionEngine, get_sleep_engine
from app.workspace.shutdown.decision import ShutdownDecision
from app.workspace.shutdown.state import shutdown_gate


class ShutdownDecisionEngine:
    def __init__(self, sleep_engine: Optional[SleepDecisionEngine] = None) -> None:
        self._sleep = sleep_engine or get_sleep_engine()

    async def evaluate(self) -> ShutdownDecision:
        if shutdown_gate.shutting_down:
            return ShutdownDecision.block("Shutdown already in progress")
        try:
            decision = await self._sleep.evaluate()
            return ShutdownDecision(allowed=decision.should_sleep, reason=decision.reason)
        except Exception as e:  # pragma: no cover - defensive
            return ShutdownDecision.block(f"Shutdown evaluation failed; staying up ({e})")


@lru_cache(maxsize=1)
def _engine_singleton() -> ShutdownDecisionEngine:
    return ShutdownDecisionEngine()


def get_shutdown_engine() -> ShutdownDecisionEngine:
    return _engine_singleton()
