"""
SleepValidator — pure decision logic.

Given a WorkspaceActivitySignals snapshot and a SleepPolicy, decides whether the
workspace may sleep. Contains NO I/O and NO side effects, which makes it trivial
to unit-test and reason about.

Rule order (first blocker wins):
  1. Any active resource guarded by the policy blocks sleep, regardless of how
     long the workspace has been idle. Checked in a stable, meaningful order:
     AI → publishing → background tasks → uploads → websockets.
  2. If no resource is active, the idle timeout is applied: sleep is allowed only
     once `seconds_since_last_activity >= idle_timeout_seconds`.
"""

from app.workspace.sleep.decision import SleepDecision
from app.workspace.sleep.policy import SleepPolicy
from app.workspace.sleep.signals import WorkspaceActivitySignals


class SleepValidator:
    """Applies a SleepPolicy to a signals snapshot to produce a SleepDecision."""

    def validate(
        self,
        signals: WorkspaceActivitySignals,
        policy: SleepPolicy,
    ) -> SleepDecision:
        snap = signals.as_dict()

        # ── 1. Active-resource guards ──────────────────────────────────────
        if policy.block_on_ai and signals.running_ai_generations > 0:
            return SleepDecision.block("AI generation currently running", snap)

        if policy.block_on_publishing and signals.active_publishing_jobs > 0:
            return SleepDecision.block("Active publishing jobs running", snap)

        if policy.block_on_background_tasks and signals.active_background_tasks > 0:
            return SleepDecision.block("Active background tasks running", snap)

        if policy.block_on_uploads and signals.pending_uploads > 0:
            return SleepDecision.block("Pending uploads in progress", snap)

        if policy.block_on_websocket and signals.active_ws_connections > 0:
            return SleepDecision.block("Active WebSocket connections present", snap)

        # ── 2. Idle timeout ────────────────────────────────────────────────
        secs = signals.seconds_since_last_activity
        if secs is None:
            # No recorded activity at all — treat as idle (safe to sleep).
            return SleepDecision.allow("No recorded activity; workspace idle", snap)

        if secs < policy.idle_timeout_seconds:
            remaining = int(policy.idle_timeout_seconds - secs)
            return SleepDecision.block(
                f"Workspace active within configured timeout ({remaining}s remaining)",
                snap,
            )

        return SleepDecision.allow("Workspace inactive for configured timeout", snap)
