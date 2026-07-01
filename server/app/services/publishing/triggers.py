"""
Publish triggers — the delivery seam between scheduling and publishing.

Three implementations:

  • DirectTrigger    — calls the publishing service in-process (synchronous-ish,
                       fire-and-forget task). Simplest; good for tests/manual calls.
  • QueuedTrigger    — TRUE event-driven, in-process: pushes post ids onto an
                       asyncio.Queue; a background consumer drains it and publishes.
                       This is the in-process analog of EventBridge → SQS → worker,
                       and is the default for the refactored architecture.
  • AWSEventBridgeTrigger / AWSSQSTrigger — PREPARED STUBS ONLY. No AWS code.
                       They document exactly where the cloud integration plugs in.

Swapping the trigger changes the delivery mechanism without touching the
scheduler or the publishing service.
"""

import asyncio
from typing import Optional

from app.services.publishing.interfaces import IPublishTrigger, IPublishingService


# ── In-process: direct ────────────────────────────────────────────────────────

class DirectTrigger(IPublishTrigger):
    """Invokes the publishing service directly (fire-and-forget task)."""

    def __init__(self, service: IPublishingService):
        self._service = service

    async def fire(self, post_id: str) -> None:
        # Fire-and-forget so the caller (scheduler) is never blocked by publishing.
        asyncio.create_task(self._safe_publish(post_id))

    async def _safe_publish(self, post_id: str):
        try:
            await self._service.publish_post(post_id)
        except Exception as e:
            print(f"[trigger:direct] publish failed for {post_id}: {e}")


# ── In-process: event-driven queue ────────────────────────────────────────────

class QueuedTrigger(IPublishTrigger):
    """
    Event-driven, in-process delivery.

    `fire()` only enqueues (O(1), non-blocking). A background consumer task
    drains the queue and calls the publishing service. This mirrors a real
    message-bus topology (producer → queue → consumer) so the move to SQS later
    is a swap of this one class.
    """

    def __init__(self, service: IPublishingService, max_queue: int = 5000):
        self._service = service
        self._queue: "asyncio.Queue[str]" = asyncio.Queue(maxsize=max_queue)
        self._consumer: Optional[asyncio.Task] = None
        self._running = False
        self._dropped = 0

    async def fire(self, post_id: str) -> None:
        try:
            self._queue.put_nowait(post_id)
        except asyncio.QueueFull:
            self._dropped += 1
            print(f"[trigger:queued] queue full — dropped {post_id}")

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._consumer = asyncio.create_task(self._consume_loop())

    def stop(self) -> None:
        self._running = False
        if self._consumer:
            self._consumer.cancel()
            self._consumer = None

    async def _consume_loop(self):
        while self._running:
            try:
                post_id = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                await self._service.publish_post(post_id)
            except Exception as e:
                print(f"[trigger:queued] publish failed for {post_id}: {e}")

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "queued": self._queue.qsize(),
            "dropped": self._dropped,
        }


# ── AWS: prepared stubs (NOT integrated) ──────────────────────────────────────

class AWSEventBridgeTrigger(IPublishTrigger):
    """
    PREPARED STUB — not implemented.

    Intended future behaviour: `fire()` puts a `PublishPost` event onto an
    Amazon EventBridge bus. A rule routes it to a target (Lambda / SQS) whose
    handler calls `PostPublishingService.publish_post(post_id)`.

    Integration points (left intentionally empty — no boto3, no AWS here):
        • __init__: accept event_bus_name, source, detail_type, region
        • fire():   build the event detail {"post_id": post_id} and put_events()
    """

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "AWSEventBridgeTrigger is a prepared stub. Implement put_events() "
            "with boto3 when wiring AWS. The rest of the system already calls "
            "fire(post_id) — only this class changes."
        )

    async def fire(self, post_id: str) -> None:  # pragma: no cover - stub
        raise NotImplementedError


class AWSSQSTrigger(IPublishTrigger):
    """
    PREPARED STUB — not implemented.

    Intended future behaviour: `fire()` sends a message {"post_id": post_id} to
    an SQS queue; a worker/Lambda consumes it and calls publish_post(post_id).

    Integration points (no boto3 here):
        • __init__: accept queue_url, region
        • fire():   send_message(QueueUrl=..., MessageBody=json({"post_id": ...}))
    """

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "AWSSQSTrigger is a prepared stub. Implement send_message() with "
            "boto3 when wiring AWS."
        )

    async def fire(self, post_id: str) -> None:  # pragma: no cover - stub
        raise NotImplementedError
