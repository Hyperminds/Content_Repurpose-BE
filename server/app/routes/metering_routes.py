"""
Metering reporting routes.

Read-only endpoints exposing the metered usage data. Admin-scoped.
These are intentionally excluded from metering itself (see middleware skip list)
to avoid self-referential noise.
"""

from fastapi import APIRouter, Depends, Query
from app.utils.jwt_handler import get_current_user, require_role
from app.services.metering_service import (
    get_usage_summary,
    get_endpoint_breakdown,
    get_recent_events,
    get_worker_stats,
)
from app.core.identity import org_id_from_user as _org_of

router = APIRouter(prefix="/metering", tags=["metering"])


@router.get("/summary")
async def metering_summary(user: dict = Depends(get_current_user)):
    """Aggregate metered usage for the caller's organization."""
    return await get_usage_summary(_org_of(user))


@router.get("/me")
async def my_metering(user: dict = Depends(get_current_user)):
    """Aggregate metered usage for the current user only."""
    return await get_usage_summary(_org_of(user), user_id=user.get("user_id"))


@router.get("/endpoints")
async def metering_endpoints(
    limit: int = Query(20, le=100),
    user: dict = Depends(get_current_user),
):
    """Per-endpoint usage breakdown for the caller's organization."""
    return await get_endpoint_breakdown(_org_of(user), limit)


@router.get("/events")
async def metering_events(
    limit: int = Query(50, le=200),
    user: dict = Depends(require_role(["super_admin"])),
):
    """Recent raw metering events (admin only)."""
    return await get_recent_events(_org_of(user), limit)


@router.get("/worker")
async def metering_worker_health(user: dict = Depends(require_role(["super_admin"]))):
    """Background metering worker health/diagnostics (admin only)."""
    return get_worker_stats()
