"""
Lightweight in-process event bus for real-time updates via SSE.
Emits events when posts are scheduled, published, failed, etc.
Subscribers (SSE connections) receive events in real-time.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncGenerator

# Store active SSE connections per user
_subscribers: dict[str, list[asyncio.Queue]] = {}


def subscribe(user_id: str) -> asyncio.Queue:
    """Subscribe a user to real-time events. Returns a queue to listen on."""
    queue = asyncio.Queue()
    if user_id not in _subscribers:
        _subscribers[user_id] = []
    _subscribers[user_id].append(queue)
    return queue


def unsubscribe(user_id: str, queue: asyncio.Queue):
    """Remove a subscriber."""
    if user_id in _subscribers:
        _subscribers[user_id] = [q for q in _subscribers[user_id] if q is not queue]
        if not _subscribers[user_id]:
            del _subscribers[user_id]


async def emit(user_id: str, event_type: str, data: dict):
    """Emit an event to all subscribers of a user."""
    if user_id not in _subscribers:
        return

    payload = json.dumps({
        "type": event_type,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    dead_queues = []
    for queue in _subscribers[user_id]:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            dead_queues.append(queue)

    # Clean up dead queues
    for q in dead_queues:
        _subscribers[user_id].remove(q)


# ============ EVENT HELPERS ============ #

async def emit_post_scheduled(user_id: str, post: dict):
    await emit(user_id, "post_scheduled", post)


async def emit_post_published(user_id: str, post: dict):
    await emit(user_id, "post_published", post)


async def emit_post_failed(user_id: str, post: dict):
    await emit(user_id, "post_failed", post)


async def emit_account_expired(user_id: str, platform: str, account_name: str):
    await emit(user_id, "account_expired", {"platform": platform, "account_name": account_name})
