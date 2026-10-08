"""API routes for the Facebook user-assisted Hermes workflow (profile + page).

Endpoints (all tenant-scoped via JWT; feature-flag gated):
  POST /social-publishing/facebook/posts         — prepare a post (stops at ACTION_REQUIRED)
  POST /social-publishing/facebook/confirm/{id}  — user confirms → post + verify
  POST /social-publishing/facebook/cancel/{id}   — user cancels a pending post
  GET  /social-publishing/facebook/pending       — list awaiting confirmations

These exist ALONGSIDE the native Meta/Facebook API path — they are only for
Facebook accounts using provider_type=user_assisted_agent. The final publish
happens ONLY on explicit confirm. No route ever publishes on its own. Browser
code lives entirely behind the service/adapter.

ONE implementation serves BOTH targets (personal profile and Facebook Page); the
target is resolved from the connected account (or an explicit request override).

Gated by BOTH ENABLE_HERMES_PROVIDER and ENABLE_HERMES_FACEBOOK (else 404).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

import app.config as _cfg
from app.services.logger import log
from app.utils.jwt_handler import get_current_user
from app.social_publishing.api.dependencies import resolve_tenant_id
from app.social_publishing.providers.hermes.facebook.service import FacebookUserAssistedService
# The media materializer is platform-agnostic (URL -> validated local file); it
# is reused as-is by Facebook. It lives under the instagram package where it was
# first introduced.
from app.social_publishing.providers.hermes.instagram.media_materializer import (
    MediaMaterializeError,
    materialize_media_urls,
    cleanup_operation_media,
)

router = APIRouter(prefix="/social-publishing/facebook", tags=["social-publishing-facebook"])

_service = FacebookUserAssistedService()


def _require_flag() -> None:
    if not getattr(_cfg, "ENABLE_HERMES_PROVIDER", False) or not getattr(
        _cfg, "ENABLE_HERMES_FACEBOOK", False
    ):
        raise HTTPException(status_code=404, detail="Facebook browser publishing is not enabled")


async def _operation_id_for(confirmation_id: str, tenant_id: str) -> Optional[str]:
    """Read-only lookup of a pending confirmation's operation_id (for media cleanup)."""
    try:
        pending = await _service._pending.find(confirmation_id, tenant_id)
    except Exception:
        return None
    return getattr(pending, "operation_id", None) if pending else None


# ── Request models ──────────────────────────────────────────────────────────

class CreateFacebookPostRequest(BaseModel):
    account_id: str = Field(..., min_length=1)
    # Post text. Optional only when an image is provided (validator enforces that
    # a post has text and/or one image).
    text: str = ""
    # Media is OPTIONAL for Facebook (text-only posts allowed). At most one image.
    # Supplied either as app-content URLs (materialized) or local server paths.
    media_paths: Optional[list[str]] = None
    media_urls: Optional[list[str]] = None
    # Optional explicit target override; otherwise resolved from the account.
    target_type: Optional[str] = None            # "profile" | "page"
    target_identifier: Optional[str] = None       # page id/vanity/URL
    operation_id: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/posts")
async def create_facebook_post(
    req: CreateFacebookPostRequest,
    user: dict = Depends(get_current_user),
):
    """
    Prepare a Facebook post and STOP for user confirmation.

    Never publishes. Returns ACTION_REQUIRED with a preview + confirmation_id.
    """
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    operation_id = req.operation_id or f"facebook_{uuid.uuid4().hex[:16]}"

    # Resolve media (optional) to LOCAL file paths. UI sends media_urls; dev
    # tooling may send media_paths directly.
    media_paths = req.media_paths or []
    materialized = False
    if not media_paths and req.media_urls:
        try:
            media_paths = materialize_media_urls(req.media_urls, operation_id)
            materialized = True
        except MediaMaterializeError as e:
            cleanup_operation_media(operation_id)
            log.warning("Facebook media materialization failed",
                        op=operation_id, err=type(e).__name__)
            return {
                "status": "failed",
                "message": f"Could not prepare the Facebook image: {e}",
            }

    result = await _service.prepare(
        tenant_id=tenant_id,
        account_id=req.account_id,
        operation_id=operation_id,
        text=req.text,
        media_paths=media_paths,
        target_type=req.target_type or "",
        target_identifier=req.target_identifier or "",
    )
    # If prepare did not reach ACTION_REQUIRED, no pending record persists the
    # files for a later confirm — clean up media we materialized from URLs.
    if materialized and result.get("status") != "action_required":
        cleanup_operation_media(operation_id)
    return result


@router.post("/confirm/{confirmation_id}")
async def confirm_facebook_post(
    confirmation_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Explicit user confirmation → resume the SAME operation, post, verify.

    This is the ONLY path that clicks Facebook's Post button.
    """
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    operation_id = await _operation_id_for(confirmation_id, tenant_id)
    result = await _service.confirm(tenant_id, confirmation_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Pending confirmation not found")
    if operation_id and result.get("status") in ("published", "failed"):
        cleanup_operation_media(operation_id)
    return result


@router.post("/cancel/{confirmation_id}")
async def cancel_facebook_post(
    confirmation_id: str,
    user: dict = Depends(get_current_user),
):
    """Cancel a pending Facebook post without publishing."""
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    operation_id = await _operation_id_for(confirmation_id, tenant_id)
    result = await _service.cancel(tenant_id, confirmation_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Pending confirmation not found")
    if operation_id:
        cleanup_operation_media(operation_id)
    return result


@router.get("/pending")
async def list_pending_facebook_posts(user: dict = Depends(get_current_user)):
    """List Facebook posts awaiting the current tenant's confirmation."""
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    return {"pending": await _service.list_pending(tenant_id)}
