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
    get_daily_usage,
    get_monthly_usage,
    get_organization_usage,
    get_user_usage,
)
from app.core.identity import org_id_from_user as _org_of

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


# ── Enterprise aggregation endpoints ──────────────────────────────────────────

@router.get("/daily")
async def daily_usage(
    days: int = Query(30, le=365),
    scope: str = Query("user", description="'user' or 'organization'"),
    campaign_id: str = Query(None),
    user: dict = Depends(get_current_user),
):
    """Daily token + cost rollup. Scope to the current user or their organization."""
    if scope == "organization":
        return await get_daily_usage(organization_id=_org_of(user), campaign_id=campaign_id, days=days)
    return await get_daily_usage(user_id=user["user_id"], campaign_id=campaign_id, days=days)


@router.get("/monthly")
async def monthly_usage(
    months: int = Query(12, le=36),
    scope: str = Query("user", description="'user' or 'organization'"),
    campaign_id: str = Query(None),
    user: dict = Depends(get_current_user),
):
    """Monthly token + cost rollup. Scope to the current user or their organization."""
    if scope == "organization":
        return await get_monthly_usage(organization_id=_org_of(user), campaign_id=campaign_id, months=months)
    return await get_monthly_usage(user_id=user["user_id"], campaign_id=campaign_id, months=months)


@router.get("/organization")
async def organization_usage(user: dict = Depends(get_current_user)):
    """Full organization rollup: totals + breakdown by user, platform, and model."""
    return await get_organization_usage(_org_of(user))


@router.get("/user")
async def user_usage(user: dict = Depends(get_current_user)):
    """Full per-user rollup: totals + breakdown by platform, model, and campaign."""
    return await get_user_usage(user["user_id"])
