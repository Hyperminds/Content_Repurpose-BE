"""
Event-driven publishing package.

Separates three previously-coupled concerns:

    Scheduler   →   Trigger   →   PublishingService
    (WHEN)          (DELIVERY)    (HOW + side effects)

- A Scheduler decides *when* a post should be published (it discovers due posts).
- A Trigger is the delivery seam — it carries a "publish this post" signal from
  whatever decided it's time, to whatever actually publishes. This is the layer
  you swap to go from in-process to AWS (EventBridge/SQS) WITHOUT touching the
  scheduler or the publishing service.
- A PublishingService exposes `publish_post(post_id)` and does the actual work.
  It does not know — and does not care — who called it (scheduler, queue
  consumer, manual API call, retry, or a future Lambda).

Nothing here integrates AWS. The AWS-specific classes are prepared stubs only.
"""

from app.services.publishing.interfaces import (
    IPublishingService,
    IPublishTrigger,
    IScheduler,
    PublishOutcome,
    PublishResult,
)

__all__ = [
    "IPublishingService",
    "IPublishTrigger",
    "IScheduler",
    "PublishOutcome",
    "PublishResult",
]
