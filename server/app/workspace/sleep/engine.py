"""
SleepDecisionEngine — orchestrator for the Sleep decision.

Composes the three collaborators:
    SignalCollector  → gathers the current activity snapshot
    SleepPolicy      → the configurable rules (idle timeout + guards)
    SleepValidator   → pure logic that turns (signals, policy) into a decision

This engine only DECIDES. It never stops anything — actually stopping the
EC2 instance is a separate concern handled elsewhere (and deliberately not part
of this module). No AWS code here.

Usage:
    engine = get_sleep_engine()
    decision = await engine.evaluate()
    if decision.should_sleep:
        ...  # hand off to the (future) instance-stop actor
"""

from functools import lru_cache
from typing import Optional

from app.workspace.sleep.decision import SleepDecision
from app.workspace.sleep.policy import SleepPolicy
from app.workspace.sleep.signals import SignalCollector, WorkspaceActivitySignals
from app.workspace.sleep.validator import SleepValidator


class SleepDecisionEngine:
    """Decides whether the workspace is allowed to enter Sleep."""

    def __init__(
        self,
        collector: Optional[SignalCollector] = None,
        validator: Optional[SleepValidator] = None,
        policy: Optional[SleepPolicy] = None,
    ) -> None:
        self._collector = collector or SignalCollector()
        self._validator = validator or SleepValidator()
        # A None policy means "read fresh from config on every evaluation" so
        # env changes take effect without a restart. A provided policy is fixed
        # (handy for tests / one-off evaluations).
        self._fixed_policy = policy

    def _resolve_policy(self) -> SleepPolicy:
        return self._fixed_policy or SleepPolicy.from_config()

    async def get_signals(self) -> WorkspaceActivitySignals:
        """Expose the current activity snapshot (diagnostics / observability)."""
        return await self._collector.collect()

    async def evaluate(self, policy: Optional[SleepPolicy] = None) -> SleepDecision:
        """
        Produce a sleep decision from live signals.

        Fail-safe: if signal collection or validation raises unexpectedly, we
        default to BLOCK (never sleep on uncertainty) so we can't accidentally
        stop an instance that might still be doing work.
        """
        try:
            active_policy = policy or self._resolve_policy()
            signals = await self._collector.collect()
            return self._validator.validate(signals, active_policy)
        except Exception as e:  # pragma: no cover - defensive
            return SleepDecision.block(f"Sleep evaluation failed; staying awake ({e})")


@lru_cache(maxsize=1)
def _engine_singleton() -> SleepDecisionEngine:
    return SleepDecisionEngine()


def get_sleep_engine() -> SleepDecisionEngine:
    """FastAPI dependency provider / accessor for the shared engine."""
    return _engine_singleton()
