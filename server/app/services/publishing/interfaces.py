"""
Abstractions for the event-driven publishing system.

These interfaces are the contract that decouples scheduling, delivery, and
publishing. Concrete implementations (polling scheduler, in-process queue,
AWS stubs) depend on these — never on each other.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


# ── Result types ──────────────────────────────────────────────────────────────

class PublishOutcome(str, Enum):
    """Terminal outcome of a single publish attempt."""
    PUBLISHED = "published"               # auto-published via platform adapter
    AWAITING_MANUAL = "awaiting_manual"   # manual-assisted platform; queued for user
    FAILED = "failed"                     # adapter error / exception
    SKIPPED = "skipped"                   # not found, wrong status, already terminal


@dataclass
class PublishResult:
    """Outcome of `IPublishingService.publish_post`."""
    post_id: str
    outcome: PublishOutcome
    platform: Optional[str] = None
    platform_post_id: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.outcome in (PublishOutcome.PUBLISHED, PublishOutcome.AWAITING_MANUAL)


# ── Publishing interface ──────────────────────────────────────────────────────

class IPublishingService(ABC):
    """
    Publishes a single post.

    The single entry point is `publish_post(post_id)`. It is intentionally
    caller-agnostic: a polling scheduler, a queue consumer, a manual API call,
    a retry job, or a future AWS Lambda all call the exact same method with
    nothing but an id.
    """

    @abstractmethod
    async def publish_post(self, post_id: str) -> PublishResult:
        """Load the post, publish it, persist status, emit side effects."""
        raise NotImplementedError


# ── Trigger interface ─────────────────────────────────────────────────────────

class IPublishTrigger(ABC):
    """
    Delivery seam between "it's time to publish" and "publish it".

    A trigger receives a post id and ensures the publish happens — synchronously,
    via an in-process queue, or by handing off to an external bus (SQS, EventBridge).
    The producer (a scheduler) never knows which mechanism is behind it.
    """

    @abstractmethod
    async def fire(self, post_id: str) -> None:
        """Request that `post_id` be published. Delivery is implementation-defined."""
        raise NotImplementedError

    def start(self) -> None:
        """Optional: start any consumer/background machinery. No-op by default."""
        return None

    def stop(self) -> None:
        """Optional: stop consumer/background machinery. No-op by default."""
        return None


# ── Scheduler interface ───────────────────────────────────────────────────────

class IScheduler(ABC):
    """
    Decides WHEN posts should publish and fires a trigger for each due post.

    A scheduler MUST NOT publish anything itself. Its only job is discovery +
    dispatch through an `IPublishTrigger`.
    """

    @abstractmethod
    def start(self) -> None:
        """Begin scheduling (e.g. start the polling loop)."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        """Stop scheduling."""
        raise NotImplementedError

    @abstractmethod
    async def discover_due_posts(self) -> List[str]:
        """Return the ids of posts that are due to publish right now."""
        raise NotImplementedError
