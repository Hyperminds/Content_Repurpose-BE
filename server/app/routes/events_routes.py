"""
Real-time events (SSE) and notifications routes.
"""

import asyncio
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from app.utils.jwt_handler import get_current_user
from app.services.event_bus import subscribe, unsubscribe
from app.services.notification_service import (
    get_notifications,
    get_unread_count,
    mark_as_read,
    mark_all_read,
)

router = APIRouter(tags=["events"])


# ============ SERVER-SENT EVENTS ============ #

@router.get("/events/stream")
async def event_stream(user: dict = Depends(get_current_user)):
    """SSE endpoint - streams real-time events to the client."""
    user_id = user["user_id"]
    queue = subscribe(user_id)

    async def generate():
        try:
            # Send initial heartbeat
            yield f"data: {{\"type\": \"connected\", \"data\": {{}}}}\n\n"

            while True:
                try:
                    # Wait for events with timeout (heartbeat every 30s)
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield f": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            unsubscribe(user_id, queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============ NOTIFICATIONS ============ #

@router.get("/notifications")
async def list_notifications(
    limit: int = Query(20),
    unread_only: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    """Get user notifications."""
    return await get_notifications(user["user_id"], limit, unread_only)


@router.get("/notifications/count")
async def notification_count(user: dict = Depends(get_current_user)):
    """Get unread notification count."""
    count = await get_unread_count(user["user_id"])
    return {"unread": count}


@router.put("/notifications/{notification_id}/read")
async def read_notification(notification_id: str, user: dict = Depends(get_current_user)):
    """Mark a notification as read."""
    return await mark_as_read(user["user_id"], notification_id)


@router.put("/notifications/read-all")
async def read_all_notifications(user: dict = Depends(get_current_user)):
    """Mark all notifications as read."""
    return await mark_all_read(user["user_id"])
