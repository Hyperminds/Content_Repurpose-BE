"""
Publishing routes - API endpoints for the multi-platform publishing system.
"""

from fastapi import APIRouter, Body, Query, Depends, HTTPException
from app.controllers.publishing_controller import (
    handle_publish_now,
    handle_schedule_post,
    handle_mark_published,
    handle_retry_post,
    handle_get_post_history,
    handle_get_post_detail,
    handle_update_post_status,
    handle_delete_post,
    handle_get_catalog,
    handle_update_catalog,
    handle_get_stats,
    handle_get_manual_payload,
)
from app.utils.jwt_handler import get_current_user, require_role

router = APIRouter(prefix="/publishing", tags=["publishing"])


# ============ INSTANT PUBLISHING ============ #

@router.post("/publish-now")
async def publish_now(data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Instantly publish content to a platform."""
    result = await handle_publish_now(data, user["user_id"])
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ============ SCHEDULING ============ #

@router.post("/schedule")
async def schedule_post(data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Schedule a post for future publishing."""
    result = await handle_schedule_post(data, user["user_id"])
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ============ MANUAL PUBLISHING ============ #

@router.post("/manual-payload/{platform}")
async def get_manual_payload(platform: str, data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Get manual publishing payload with instructions."""
    result = await handle_get_manual_payload(platform, data)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.put("/mark-published/{post_id}")
async def mark_published(post_id: str, user: dict = Depends(get_current_user)):
    """Mark a manual-assisted post as manually published."""
    result = await handle_mark_published(post_id, user["user_id"])
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ============ RETRY ============ #

@router.post("/retry/{post_id}")
async def retry_post(post_id: str, user: dict = Depends(get_current_user)):
    """Retry a failed post."""
    result = await handle_retry_post(post_id, user["user_id"])
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ============ POST HISTORY ============ #

@router.get("/history")
async def get_post_history(
    platform: str = Query(None),
    status: str = Query(None),
    publish_type: str = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
    user: dict = Depends(get_current_user),
):
    """Get post history with optional filters."""
    return await handle_get_post_history(
        user["user_id"], platform, status, publish_type, limit, offset
    )


@router.get("/history/{post_id}")
async def get_post_detail(post_id: str, user: dict = Depends(get_current_user)):
    """Get single post detail."""
    result = await handle_get_post_detail(post_id, user["user_id"])
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.put("/history/{post_id}/status")
async def update_post_status(post_id: str, data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Update post status."""
    result = await handle_update_post_status(post_id, user["user_id"], data)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/history/{post_id}")
async def delete_post(post_id: str, user: dict = Depends(get_current_user)):
    """Delete a post history entry."""
    result = await handle_delete_post(post_id, user["user_id"])
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ============ PLATFORM CATALOG ============ #

@router.get("/platforms")
async def get_platform_catalog(user: dict = Depends(get_current_user)):
    """Get all platforms in the catalog."""
    return await handle_get_catalog()


@router.put("/platforms/{platform_name}")
async def update_platform(
    platform_name: str,
    data: dict = Body(...),
    user: dict = Depends(require_role(["super_admin"])),
):
    """Update platform catalog entry (admin only)."""
    result = await handle_update_catalog(platform_name, data)
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ============ DASHBOARD / STATS ============ #

@router.get("/stats")
async def get_stats(user: dict = Depends(get_current_user)):
    """Get publishing statistics for dashboard."""
    return await handle_get_stats(user["user_id"])
