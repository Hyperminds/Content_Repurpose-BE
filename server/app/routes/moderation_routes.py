"""
Moderation routes - disclaimer acceptance, user status checks, admin logs.
"""

from fastapi import APIRouter, Depends, HTTPException
from app.utils.jwt_handler import get_current_user, require_role
from app.services.moderation_service import (
    accept_disclaimer,
    get_disclaimer_status,
    check_user_status,
    get_moderation_logs,
)

router = APIRouter(prefix="/moderation", tags=["moderation"])


@router.get("/disclaimer")
async def disclaimer_status(user: dict = Depends(get_current_user)):
    """Check if user has accepted the disclaimer."""
    return await get_disclaimer_status(user["user_id"])


@router.post("/disclaimer/accept")
async def accept(user: dict = Depends(get_current_user)):
    """Accept the platform disclaimer."""
    return await accept_disclaimer(user["user_id"])


@router.get("/status")
async def user_moderation_status(user: dict = Depends(get_current_user)):
    """Check user's moderation status."""
    return await check_user_status(user["user_id"])


@router.get("/logs")
async def moderation_logs(user: dict = Depends(require_role(["super_admin"]))):
    """Get moderation logs (admin only)."""
    return await get_moderation_logs()
