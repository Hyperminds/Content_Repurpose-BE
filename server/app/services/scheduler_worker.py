"""
Scheduler worker — composition root for the event-driven publishing system.

This module no longer contains any publishing logic. It only wires the pieces
together and preserves the original public API (`start_scheduler`,
`stop_scheduler`, `_running`) so the rest of the app (main.py lifespan and the
health check) is unaffected.

Topology (in-process, event-driven):

    PollingScheduler ──fire(post_id)──▶ QueuedTrigger ──▶ PostPublishingService
        (WHEN)                            (DELIVERY)            (HOW)

To move to AWS later, swap QueuedTrigger → AWSSQSTrigger / AWSEventBridgeTrigger
and/or PollingScheduler → AWSEventDrivenScheduler. Nothing else changes.
"""

from app.services.publishing.service import PostPublishingService
from app.services.publishing.triggers import QueuedTrigger
from app.services.publishing.schedulers import PollingScheduler

# ── Composition ───────────────────────────────────────────────────────────────
_service = PostPublishingService()
_trigger = QueuedTrigger(_service)
_scheduler = PollingScheduler(_trigger, interval_seconds=30)

# Preserved for the health check: `from app.services.scheduler_worker import _running`
_running = False


def get_publishing_service() -> PostPublishingService:
    """
    Expose the shared publishing service so any caller (e.g. a manual
    'publish now by id' endpoint or a retry job) can publish through the exact
    same `publish_post(post_id)` entry point the scheduler uses.
    """
    return _service


def start_scheduler():
    """Start the trigger consumer and the polling scheduler."""
    global _running
    if _running:
        return
    _trigger.start()      # start the queue consumer (delivery side)
    _scheduler.start()    # start the polling loop (scheduling side)
    _running = True


def stop_scheduler():
    """Stop the polling scheduler and the trigger consumer."""
    global _running
    _scheduler.stop()
    _trigger.stop()
    _running = False
