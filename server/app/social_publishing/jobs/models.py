"""Job queue domain models and enums.

These are the production-grade job tracking types used by the scheduler,
worker, and retry system. Separate from the lighter-weight PublishingJob
in the domain layer (which serves as an API-facing audit record).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from app.social_publishing.domain.enums import SocialPlatform


class JobStatus(str, Enum):
    """Lifecycle states for a publishing job in the queue."""

    PENDING = "pending"          # Created, waiting to be claimed
    CLAIMED = "claimed"          # Locked by a worker, about to execute
    EXECUTING = "executing"      # Actively publishing
    COMPLETED = "completed"      # Successfully published
    FAILED = "failed"            # Permanently failed (no more retries)
    RETRYING = "retrying"        # Failed but will be retried
    CANCELLED = "cancelled"      # Cancelled before execution

    # ── Hybrid-publishing job states (Phase 10) ───────────────────────────────
    VERIFYING = "verifying"              # Confirming an uncertain outcome
    ACTION_REQUIRED = "action_required"  # Needs a manual user step
    WAITING_FOR_USER = "waiting_for_user"  # Paused pending user interaction
    UNKNOWN = "unknown"                  # Outcome uncertain — MUST be verified,
                                         # NEVER auto-claimed for a blind retry


class FailureCategory(str, Enum):
    """Classification of publishing failures for retry decisions."""

    TEMPORARY = "temporary"             # Network timeout, 5xx, transient
    PERMANENT = "permanent"             # Invalid content, 4xx (not auth)
    AUTH_EXPIRED = "auth_expired"       # 401/403 — token needs refresh
    RATE_LIMITED = "rate_limited"       # 429 — back off per platform
    PLATFORM_DOWN = "platform_down"    # Platform API unavailable
    UNKNOWN = "unknown"                 # Unclassified failure


# Maximum number of publish attempts before permanent failure
MAX_ATTEMPTS = 5

# Lock timeout: if a job stays CLAIMED longer than this, it's considered stuck
LOCK_TIMEOUT_SECONDS = 300  # 5 minutes


@dataclass
class QueuedJob:
    """A publishing job in the queue with full tracking metadata."""

    id: str
    tenant_id: str
    post_id: str
    account_id: str
    platform: SocialPlatform
    idempotency_key: str

    status: JobStatus = JobStatus.PENDING
    attempts: int = 0
    max_attempts: int = MAX_ATTEMPTS

    # Scheduling
    scheduled_at: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None

    # Locking (for worker claiming)
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None

    # Execution tracking
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Result
    failure_reason: Optional[str] = None
    failure_category: Optional[FailureCategory] = None
    external_post_id: Optional[str] = None

    # Timestamps
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
