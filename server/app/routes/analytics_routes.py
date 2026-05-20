"""
Analytics routes - detailed post analytics, AI insights, platform performance.
"""

from fastapi import APIRouter, Depends, Query, HTTPException
from app.utils.jwt_handler import get_current_user
from app.services.analytics_service import (
    get_posts_by_status,
    get_post_detail_analytics,
    get_platform_performance,
    get_posting_timeline,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/posts/{status}")
async def posts_by_status(
    status: str,
    limit: int = Query(50),
    user: dict = Depends(get_current_user),
):
    """Get posts filtered by status with analytics."""
    valid = ["published", "scheduled", "failed", "pending_manual", "posted"]
    if status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Use: {', '.join(valid)}")
    return await get_posts_by_status(user["user_id"], status, limit)


@router.get("/post/{post_id}")
async def post_detail(post_id: str, user: dict = Depends(get_current_user)):
    """Get detailed analytics + AI insights for a single post."""
    result = await get_post_detail_analytics(user["user_id"], post_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/platforms")
async def platform_performance(user: dict = Depends(get_current_user)):
    """Get performance breakdown by platform."""
    return await get_platform_performance(user["user_id"])


@router.get("/timeline")
async def posting_timeline(
    days: int = Query(14),
    user: dict = Depends(get_current_user),
):
    """Get posting activity timeline."""
    return await get_posting_timeline(user["user_id"], days)
