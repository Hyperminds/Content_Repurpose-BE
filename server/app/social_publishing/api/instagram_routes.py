"""API routes for the Instagram user-assisted Hermes workflow.

Endpoints (all tenant-scoped via JWT; feature-flag gated):
  POST /social-publishing/instagram/posts         — prepare a post (stops at ACTION_REQUIRED)
  POST /social-publishing/instagram/confirm/{id}  — user confirms → share + verify
  POST /social-publishing/instagram/cancel/{id}   — user cancels a pending post
  GET  /social-publishing/instagram/pending       — list awaiting confirmations

These exist ALONGSIDE the native Meta Instagram API path — they are only for
Instagram accounts using provider_type=user_assisted_agent. The final publish
happens ONLY on explicit confirm. No route ever publishes on its own. Browser
code lives entirely behind the service/adapter.

Gated by BOTH ENABLE_HERMES_PROVIDER and ENABLE_HERMES_INSTAGRAM (else 404).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

import app.config as _cfg
from app.services.logger import log
from app.utils.jwt_handler import get_current_user
from app.social_publishing.api.dependencies import resolve_tenant_id
from app.social_publishing.providers.hermes.instagram.service import InstagramUserAssistedService
from app.social_publishing.providers.hermes.instagram.media_materializer import (
    MediaMaterializeError,
    materialize_media_urls,
    cleanup_operation_media,
)

router = APIRouter(prefix="/social-publishing/instagram", tags=["social-publishing-instagram"])

_service = InstagramUserAssistedService()


def _require_flag() -> None:
    if not getattr(_cfg, "ENABLE_HERMES_PROVIDER", False) or not getattr(
        _cfg, "ENABLE_HERMES_INSTAGRAM", False
    ):
        raise HTTPException(status_code=404, detail="Instagram browser publishing is not enabled")


async def _operation_id_for(confirmation_id: str, tenant_id: str) -> Optional[str]:
    """Read-only lookup of a pending confirmation's operation_id (for media cleanup)."""
    try:
        pending = await _service._pending.find(confirmation_id, tenant_id)
    except Exception:
        return None
    return getattr(pending, "operation_id", None) if pending else None


# ── Request models ──────────────────────────────────────────────────────────

class CreateInstagramPostRequest(BaseModel):
    account_id: str = Field(..., min_length=1)
    caption: str = ""
    # Media may be supplied one of two ways (exactly one required):
    #   - media_paths: server-accessible LOCAL file paths (used by dev smoke
    #     tooling that already has files on disk).
    #   - media_urls:  URLs of app-created content (a Cloudinary https URL or the
    #     app's own /uploads/files/… path). The route materializes each URL to a
    #     validated local file before preparing, since the browser attach needs a
    #     real on-disk path. This is the path the Trendzzo UI uses.
    # Instagram requires exactly one image.
    media_paths: Optional[list[str]] = None
    media_urls: Optional[list[str]] = None
    # Optional caller-supplied operation id (idempotent retries); generated if absent.
    operation_id: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/posts")
async def create_instagram_post(
    req: CreateInstagramPostRequest,
    user: dict = Depends(get_current_user),
):
    """
    Prepare an Instagram post and STOP for user confirmation.

    Never publishes. Returns ACTION_REQUIRED with a preview + confirmation_id.
    """
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    operation_id = req.operation_id or f"instagram_{uuid.uuid4().hex[:16]}"

    # Resolve media to LOCAL file paths. The UI sends media_urls (app content);
    # dev tooling may send media_paths directly. Exactly one source is required.
    media_paths = req.media_paths or []
    if not media_paths:
        if not req.media_urls:
            return {
                "status": "failed",
                "message": "An image is required to post to Instagram.",
            }
        try:
            # Materialize into a DURABLE per-operation dir so the same files are
            # still present when the user later confirms (confirm re-prepares the
            # same operation in a fresh browser session).
            media_paths = materialize_media_urls(req.media_urls, operation_id)
        except MediaMaterializeError as e:
            # No pending record was created, so nothing to resume — clean up any
            # partial files from this operation.
            cleanup_operation_media(operation_id)
            log.warning("Instagram media materialization failed",
                        op=operation_id, err=type(e).__name__)
            return {
                "status": "failed",
                "message": f"Could not prepare the Instagram image: {e}",
            }

    result = await _service.prepare(
        tenant_id=tenant_id,
        account_id=req.account_id,
        operation_id=operation_id,
        caption=req.caption,
        media_paths=media_paths,
    )
    # If prepare did not reach ACTION_REQUIRED, no pending record persists the
    # files for a later confirm — clean up the materialized media now (only when
    # we created it from URLs; caller-supplied media_paths are not ours to remove).
    if req.media_urls and result.get("status") != "action_required":
        cleanup_operation_media(operation_id)
    return result


@router.post("/confirm/{confirmation_id}")
async def confirm_instagram_post(
    confirmation_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Explicit user confirmation → resume the SAME operation, share, verify.

    This is the ONLY path that clicks Instagram's Share button.
    """
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    # Capture the operation id (read-only) so we can clean up materialized media
    # once the post reaches a terminal state. UNKNOWN keeps the files (the post
    # may need verification/retry inspection).
    operation_id = await _operation_id_for(confirmation_id, tenant_id)
    result = await _service.confirm(tenant_id, confirmation_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Pending confirmation not found")
    if operation_id and result.get("status") in ("published", "failed"):
        cleanup_operation_media(operation_id)
    return result


@router.post("/cancel/{confirmation_id}")
async def cancel_instagram_post(
    confirmation_id: str,
    user: dict = Depends(get_current_user),
):
    """Cancel a pending Instagram post without publishing."""
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
async def list_pending_instagram_posts(user: dict = Depends(get_current_user)):
    """List Instagram posts awaiting the current tenant's confirmation."""
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    return {"pending": await _service.list_pending(tenant_id)}
