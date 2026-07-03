"""
SleepEvents — the observable lifecycle of a sleep sequence.

Provides:
  • SleepEvent   — an enum naming every step/outcome of the orchestration.
  • SleepEventRecord — a timestamped record of one emitted event.
  • SleepEventLog — an in-memory collector the orchestrator emits into, giving a
    structured, ordered trace of what happened (for the response report + logs).

This is deliberately decoupled from any transport. Later it can also publish to
the WebSocket bus, an audit collection, or CloudWatch without changing callers.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class SleepEvent(str, Enum):
    """Every step + terminal outcome of the sleep sequence."""

    # Lifecycle
    SLEEP_REQUESTED = "sleep_requested"
    SLEEP_ABORTED = "sleep_aborted"          # decision said don't sleep
    SLEEP_COMPLETED = "sleep_completed"
    SLEEP_FAILED = "sleep_failed"            # unexpected orchestration error

    # Steps (in execution order)
    DECISION_VALIDATED = "decision_validated"
    METRICS_FLUSHED = "metrics_flushed"
    ACTIVITY_SAVED = "activity_saved"
    WEBSOCKETS_CLOSED = "websockets_closed"
    SCHEDULER_STOPPED = "scheduler_stopped"
    STATE_PERSISTED = "state_persisted"
    SHUTDOWN_LOGGED = "shutdown_logged"
    POWER_SLEEP_INVOKED = "power_sleep_invoked"

    # Emitted when an individual (best-effort) step fails
    STEP_FAILED = "step_failed"


@dataclass
class SleepEventRecord:
    """A single emitted event with timing + optional detail."""

    event: SleepEvent
    ok: bool = True
    detail: Optional[Any] = None
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "event": self.event.value,
            "ok": self.ok,
            "detail": self.detail,
            "at": self.at,
        }


class SleepEventLog:
    """Ordered, in-memory sink for the events of one orchestration run."""

    def __init__(self) -> None:
        self._records: list[SleepEventRecord] = []

    def emit(self, event: SleepEvent, ok: bool = True, detail: Any = None) -> SleepEventRecord:
        record = SleepEventRecord(event=event, ok=ok, detail=detail)
        self._records.append(record)
        return record

    @property
    def records(self) -> list[SleepEventRecord]:
        return list(self._records)

    def to_list(self) -> list[dict]:
        return [r.to_dict() for r in self._records]
