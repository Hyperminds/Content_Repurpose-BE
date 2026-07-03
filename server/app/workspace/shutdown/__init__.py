"""
Automatic Backend Shutdown package.

A COMPLETE shutdown (not sleep/hibernate): tears down services so the instance
can be safely stopped externally. No EC2/boto3 here — PowerController.shutdown()
logs the request for now.

    ShutdownGate            — admission control + in-flight drain tracking
    ShutdownDecision        — {allowed, reason} result
    ShutdownDecisionEngine  — is it safe to shut down? (reuses SleepDecisionEngine)
    ShutdownHooks           — the concrete teardown steps
    ShutdownOrchestrator    — runs the ordered 11-step sequence
    ShutdownWatcher         — periodic evaluate → shutdown loop (config-gated)
"""

from app.workspace.shutdown.state import ShutdownGate, ShutdownPhase, shutdown_gate
from app.workspace.shutdown.decision import ShutdownDecision
from app.workspace.shutdown.engine import ShutdownDecisionEngine, get_shutdown_engine
from app.workspace.shutdown.hooks import ShutdownHooks
from app.workspace.shutdown.orchestrator import ShutdownOrchestrator, get_shutdown_orchestrator
from app.workspace.shutdown.watcher import ShutdownWatcher, get_shutdown_watcher

__all__ = [
    "ShutdownGate",
    "ShutdownPhase",
    "shutdown_gate",
    "ShutdownDecision",
    "ShutdownDecisionEngine",
    "get_shutdown_engine",
    "ShutdownHooks",
    "ShutdownOrchestrator",
    "get_shutdown_orchestrator",
    "ShutdownWatcher",
    "get_shutdown_watcher",
]
