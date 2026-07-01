"""
Metering Service — persistence layer for Global Resource Metering.

Design goals:
  • Near-zero request-path cost — the middleware only does an O(1), non-blocking
    `enqueue()`. The actual DB write happens in a background worker.
  • Batched writes — records are bulk-inserted to minimize MongoDB round-trips.
  • Fail-safe — a full queue drops records silently; a DB error is logged and
    swallowed. Metering can never affect API availability.
  • Provider-agnostic — records are stored raw so they can later be streamed to
    OpenMeter / Lago by a separate exporter reading `exported: false`.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from app.database import db
from app.models.metering_model import serialize_metering_record

metering_collection = db["metering_events"]

# ── Tunables ──────────────────────────────────────────────────────────────────
_MAX_QUEUE = 10_000      # hard cap; beyond this we drop (back-pressure safety)
_BATCH_SIZE = 200        # max records per bulk insert
_FLUSH_INTERVAL = 2.0    # seconds between forced flushes

# ── Internal state ────────────────────────────────────────────────────────────
_queue: "Optional[asyncio.Queue]" = None
_worker_task: "Optional[asyncio.Task]" = None
_running = False
_dropped = 0


def enqueue(record: dict) -> None:
    """
    Non-blocking. Push a metering record onto the in-memory queue.
    Safe to call from the request path — returns immediately and never raises.
    """
    global _dropped
    try:
        if _queue is None:
            return  # worker not started; skip silently
        _queue.put_nowait(record)
    except asyncio.QueueFull:
        _dropped += 1
    except Exception:
        pass


async def _flush_batch(batch: list) -> None:
    """Bulk-insert a batch of records. Errors are swallowed."""
    if not batch:
        return
    try:
        await metering_collection.insert_many(batch, ordered=False)
    except Exception as e:
        # Never raise — log once and move on.
        print(f"[metering] flush failed for {len(batch)} records: {e}")


async def _worker_loop() -> None:
    """
    Background consumer. Drains the queue in batches, flushing either when the
    batch fills up or every _FLUSH_INTERVAL seconds.
    """
    global _running
    while _running:
        batch = []
        try:
            # Block for the first item (with timeout so we flush periodically)
            try:
                first = await asyncio.wait_for(_queue.get(), timeout=_FLUSH_INTERVAL)
                batch.append(first)
            except asyncio.TimeoutError:
                continue

            # Greedily drain whatever else is queued, up to batch size
            while len(batch) < _BATCH_SIZE:
                try:
                    batch.append(_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            await _flush_batch(batch)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[metering] worker error: {e}")

    # Drain anything left on shutdown
    leftover = []
    try:
        while True:
            leftover.append(_queue.get_nowait())
    except Exception:
        pass
    await _flush_batch(leftover)


def start_metering_worker() -> None:
    """Start the background flush worker. Call from app lifespan startup."""
    global _queue, _worker_task, _running
    if _running:
        return
    _queue = asyncio.Queue(maxsize=_MAX_QUEUE)
    _running = True
    _worker_task = asyncio.create_task(_worker_loop())


async def stop_metering_worker() -> None:
    """Stop the worker and flush remaining records. Call from lifespan shutdown."""
    global _running, _worker_task
    _running = False
    if _worker_task:
        try:
            await asyncio.wait_for(_worker_task, timeout=5.0)
        except Exception:
            _worker_task.cancel()
    _worker_task = None


# ── Read / analytics API (used by reporting endpoints) ────────────────────────

async def get_usage_summary(organization_id: str, user_id: str = None) -> dict:
    """Aggregate metered usage for an org (optionally a single user)."""
    match = {"organization_id": organization_id}
    if user_id:
        match["user_id"] = user_id

    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": None,
            "total_requests": {"$sum": 1},
            "total_request_bytes": {"$sum": "$request_bytes"},
            "total_response_bytes": {"$sum": "$response_bytes"},
            "total_upload_bytes": {"$sum": "$upload_bytes"},
            "total_download_bytes": {"$sum": "$download_bytes"},
            "total_ai_tokens": {"$sum": "$ai_total_tokens"},
            "total_ai_cost_usd": {"$sum": "$ai_estimated_cost_usd"},
            "avg_execution_ms": {"$avg": "$execution_time_ms"},
        }},
    ]
    results = await metering_collection.aggregate(pipeline).to_list(1)
    if not results:
        return {
            "total_requests": 0, "total_request_bytes": 0, "total_response_bytes": 0,
            "total_upload_bytes": 0, "total_download_bytes": 0,
            "total_ai_tokens": 0, "total_ai_cost_usd": 0.0, "avg_execution_ms": 0.0,
        }
    r = results[0]
    return {
        "total_requests": r.get("total_requests", 0),
        "total_request_bytes": r.get("total_request_bytes", 0),
        "total_response_bytes": r.get("total_response_bytes", 0),
        "total_upload_bytes": r.get("total_upload_bytes", 0),
        "total_download_bytes": r.get("total_download_bytes", 0),
        "total_ai_tokens": r.get("total_ai_tokens", 0),
        "total_ai_cost_usd": round(r.get("total_ai_cost_usd", 0.0), 6),
        "avg_execution_ms": round(r.get("avg_execution_ms", 0.0), 2),
    }


async def get_endpoint_breakdown(organization_id: str, limit: int = 20) -> list:
    """Per-endpoint usage breakdown for an org."""
    pipeline = [
        {"$match": {"organization_id": organization_id}},
        {"$group": {
            "_id": {"endpoint": "$endpoint", "method": "$method"},
            "count": {"$sum": 1},
            "avg_execution_ms": {"$avg": "$execution_time_ms"},
            "total_ai_cost_usd": {"$sum": "$ai_estimated_cost_usd"},
            "total_bytes": {"$sum": {"$add": ["$request_bytes", "$response_bytes"]}},
        }},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    results = await metering_collection.aggregate(pipeline).to_list(limit)
    return [
        {
            "endpoint": r["_id"]["endpoint"],
            "method": r["_id"]["method"],
            "count": r.get("count", 0),
            "avg_execution_ms": round(r.get("avg_execution_ms", 0.0), 2),
            "total_ai_cost_usd": round(r.get("total_ai_cost_usd", 0.0), 6),
            "total_bytes": r.get("total_bytes", 0),
        }
        for r in results
    ]


async def get_recent_events(organization_id: str, limit: int = 50) -> list:
    """Recent raw metering events for an org."""
    cursor = metering_collection.find(
        {"organization_id": organization_id}
    ).sort("timestamp", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [serialize_metering_record(d) for d in docs]


def get_worker_stats() -> dict:
    """Health/diagnostics for the metering worker."""
    return {
        "running": _running,
        "queued": _queue.qsize() if _queue else 0,
        "dropped": _dropped,
        "max_queue": _MAX_QUEUE,
        "batch_size": _BATCH_SIZE,
        "flush_interval_s": _FLUSH_INTERVAL,
    }
