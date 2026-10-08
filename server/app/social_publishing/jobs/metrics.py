"""Publishing metrics — in-process observability for the job system.

Tracks:
  - Jobs created (by scheduler)
  - Jobs processed (claimed by worker)
  - Jobs succeeded
  - Jobs failed (permanently)
  - Retries scheduled
  - Publishing latency (moving average)
  - Stuck jobs recovered

These metrics are exposed via an API endpoint and the existing /system/stats
infrastructure. In production, they can be scraped by Prometheus/Datadog or
pushed to CloudWatch.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import deque


# Keep last N latency samples for average calculation
_LATENCY_WINDOW_SIZE = 100


@dataclass
class PublishingMetrics:
    """Thread-safe (single-event-loop) metrics tracker for the publishing system."""

    jobs_created: int = 0
    jobs_processed: int = 0
    jobs_succeeded: int = 0
    jobs_failed: int = 0
    jobs_retried: int = 0
    stuck_jobs_recovered: int = 0
    _latency_samples: deque = field(default_factory=lambda: deque(maxlen=_LATENCY_WINDOW_SIZE))
    _started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def record_job_created(self) -> None:
        self.jobs_created += 1

    def record_success(self, elapsed_ms: int) -> None:
        self.jobs_processed += 1
        self.jobs_succeeded += 1
        self._latency_samples.append(elapsed_ms)

    def record_failure(self) -> None:
        self.jobs_processed += 1
        self.jobs_failed += 1

    def record_retry(self) -> None:
        self.jobs_processed += 1
        self.jobs_retried += 1

    def record_stuck_recovered(self, count: int) -> None:
        self.stuck_jobs_recovered += count

    @property
    def avg_latency_ms(self) -> float:
        if not self._latency_samples:
            return 0.0
        return round(sum(self._latency_samples) / len(self._latency_samples), 1)

    @property
    def success_rate(self) -> float:
        if self.jobs_processed == 0:
            return 0.0
        return round(self.jobs_succeeded / self.jobs_processed * 100, 1)

    @property
    def uptime_seconds(self) -> int:
        return int((datetime.now(timezone.utc) - self._started_at).total_seconds())

    def snapshot(self) -> dict:
        """Return a JSON-safe snapshot of all metrics."""
        return {
            "jobs_created": self.jobs_created,
            "jobs_processed": self.jobs_processed,
            "jobs_succeeded": self.jobs_succeeded,
            "jobs_failed": self.jobs_failed,
            "jobs_retried": self.jobs_retried,
            "stuck_jobs_recovered": self.stuck_jobs_recovered,
            "avg_latency_ms": self.avg_latency_ms,
            "success_rate_percent": self.success_rate,
            "uptime_seconds": self.uptime_seconds,
        }

    def reset(self) -> None:
        """Reset all counters (for testing)."""
        self.jobs_created = 0
        self.jobs_processed = 0
        self.jobs_succeeded = 0
        self.jobs_failed = 0
        self.jobs_retried = 0
        self.stuck_jobs_recovered = 0
        self._latency_samples.clear()
        self._started_at = datetime.now(timezone.utc)
