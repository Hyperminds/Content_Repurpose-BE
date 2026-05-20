"""
AI Usage tracking routes - token usage, costs, efficiency insights.
"""

from fastapi import APIRouter, Depends, Query
from app.utils.jwt_handler import get_current_user
from app.services.ai_usage_service import (
    get_usage_summary,
    get_platform_breakdown,
    get_recent_logs,
    get_efficiency_insights,
)

router = APIRouter(prefix="/ai-usage", tags=["ai-usage"])


@router.get("/summary")
async def usage_summary(user: dict = Depends(get_current_user)):
    """Get aggregate AI usage stats."""
    return await get_usage_summary(user["user_id"])


@router.get("/platforms")
async def platform_breakdown(user: dict = Depends(get_current_user)):
    """Get token usage by platform."""
    return await get_platform_breakdown(user["user_id"])


@router.get("/logs")
async def recent_logs(limit: int = Query(20), user: dict = Depends(get_current_user)):
    """Get recent generation logs."""
    return await get_recent_logs(user["user_id"], limit)


@router.get("/efficiency")
async def efficiency_insights(user: dict = Depends(get_current_user)):
    """Get AI efficiency insights."""
    return await get_efficiency_insights(user["user_id"])
