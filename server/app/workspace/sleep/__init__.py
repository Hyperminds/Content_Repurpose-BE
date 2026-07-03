"""
Sleep Decision Engine.

Determines whether the workspace is allowed to enter Sleep (a business concept
that will eventually stop the EC2 instance — that stop action lives elsewhere;
there is deliberately NO AWS code here).

Clean architecture:
    SleepPolicy          — configurable rules (idle timeout + per-guard toggles)
    SignalCollector      — gathers live activity signals (fail-safe)
    SleepValidator       — pure (signals, policy) → SleepDecision logic
    SleepDecision        — immutable result, serializes to {should_sleep, reason}
    SleepDecisionEngine  — orchestrates the above

Public entrypoint:
    from app.workspace.sleep import get_sleep_engine
    decision = await get_sleep_engine().evaluate()
"""

from app.workspace.sleep.decision import SleepDecision
from app.workspace.sleep.policy import SleepPolicy
from app.workspace.sleep.validator import SleepValidator
from app.workspace.sleep.engine import SleepDecisionEngine, get_sleep_engine
from app.workspace.sleep.signals import (
    SignalCollector,
    WorkspaceActivitySignals,
    WorkspaceActivityRegistry,
    activity_registry,
)
from app.workspace.sleep.events import SleepEvent, SleepEventRecord, SleepEventLog
from app.workspace.sleep.hooks import SleepHooks
from app.workspace.sleep.power_controller import (
    PowerController,
    NoOpPowerController,
    get_power_controller,
)
from app.workspace.sleep.orchestrator import SleepOrchestrator, get_sleep_orchestrator

__all__ = [
    "SleepDecision",
    "SleepPolicy",
    "SleepValidator",
    "SleepDecisionEngine",
    "get_sleep_engine",
    "SignalCollector",
    "WorkspaceActivitySignals",
    "WorkspaceActivityRegistry",
    "activity_registry",
    "SleepEvent",
    "SleepEventRecord",
    "SleepEventLog",
    "SleepHooks",
    "PowerController",
    "NoOpPowerController",
    "get_power_controller",
    "SleepOrchestrator",
    "get_sleep_orchestrator",
]
