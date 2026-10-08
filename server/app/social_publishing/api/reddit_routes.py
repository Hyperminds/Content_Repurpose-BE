"""API routes for the Reddit user-assisted Hermes workflow.

Endpoints (all tenant-scoped via JWT; feature-flag gated):
  POST /social-publishing/reddit/posts            — prepare a post (stops at ACTION_REQUIRED)
  POST /social-publishing/reddit/confirm/{id}     — user confirms → submit + verify
  POST /social-publishing/reddit/cancel/{id}      — user cancels a pending post
  GET  /social-publishing/reddit/pending          — list awaiting confirmations

The final publish happens ONLY on explicit confirm. No route ever publishes on
its own. Browser code lives entirely behind the service/adapter.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

import app.config as _cfg
from app.utils.jwt_handler import get_current_user
from app.core.identity import user_id_from_user
from app.social_publishing.api.dependencies import resolve_tenant_id
from app.social_publishing.providers.hermes.reddit.service import RedditUserAssistedService

router = APIRouter(prefix="/social-publishing/reddit", tags=["social-publishing-reddit"])

_service = RedditUserAssistedService()


def _require_flag() -> None:
    if not getattr(_cfg, "ENABLE_HERMES_PROVIDER", False):
        raise HTTPException(status_code=404, detail="Reddit publishing is not enabled")


# ── Request models ──────────────────────────────────────────────────────────

class CreateRedditPostRequest(BaseModel):
    account_id: str = Field(..., min_length=1)
    subreddit: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    body: str = ""
    media_paths: Optional[list[str]] = None
    # Optional caller-supplied operation id (for idempotent retries of the same
    # logical post); generated if absent.
    operation_id: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/posts")
async def create_reddit_post(
    req: CreateRedditPostRequest,
    user: dict = Depends(get_current_user),
):
    """
    Prepare a Reddit post and STOP for user confirmation.

    Never publishes. Returns ACTION_REQUIRED with a preview + confirmation_id.
    """
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    operation_id = req.operation_id or f"reddit_{uuid.uuid4().hex[:16]}"

    return await _service.prepare(
        tenant_id=tenant_id,
        account_id=req.account_id,
        operation_id=operation_id,
        title=req.title,
        body=req.body,
        subreddit=req.subreddit,
        media_paths=req.media_paths or [],
    )


@router.post("/confirm/{confirmation_id}")
async def confirm_reddit_post(
    confirmation_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Explicit user confirmation → resume the SAME operation, submit, verify.

    This is the ONLY path that clicks Reddit's Post button.
    """
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    result = await _service.confirm(tenant_id, confirmation_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Pending confirmation not found")
    return result


@router.post("/cancel/{confirmation_id}")
async def cancel_reddit_post(
    confirmation_id: str,
    user: dict = Depends(get_current_user),
):
    """Cancel a pending Reddit post without publishing."""
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    result = await _service.cancel(tenant_id, confirmation_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Pending confirmation not found")
    return result


@router.get("/pending")
async def list_pending_reddit_posts(user: dict = Depends(get_current_user)):
    """List Reddit posts awaiting the current tenant's confirmation."""
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    return {"pending": await _service.list_pending(tenant_id)}
