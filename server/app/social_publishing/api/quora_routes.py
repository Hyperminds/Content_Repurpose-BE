"""API routes for the Quora user-assisted Hermes workflow.

Endpoints (all tenant-scoped via JWT; feature-flag gated):
  POST /social-publishing/quora/posts         — prepare a post (stops at ACTION_REQUIRED)
  POST /social-publishing/quora/confirm/{id}  — user confirms → post + verify
  POST /social-publishing/quora/cancel/{id}   — user cancels a pending post
  GET  /social-publishing/quora/pending       — list awaiting confirmations

Quora has NO native API path in Trendzzo — this is the only Quora provider, and
it is strictly user-assisted. The final publish happens ONLY on explicit
confirm. No route ever publishes on its own. Browser code lives entirely behind
the service/adapter.

Gated by BOTH ENABLE_HERMES_PROVIDER and ENABLE_HERMES_QUORA (else 404).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

import app.config as _cfg
from app.services.logger import log
from app.utils.jwt_handler import get_current_user
from app.social_publishing.api.dependencies import resolve_tenant_id
from app.social_publishing.providers.hermes.quora.service import QuoraUserAssistedService
# The media materializer is platform-agnostic (URL -> validated local file); it
# is reused as-is by Quora. It lives under the instagram package where it was
# first introduced.
from app.social_publishing.providers.hermes.instagram.media_materializer import (
    MediaMaterializeError,
    materialize_media_urls,
    cleanup_operation_media,
)

router = APIRouter(prefix="/social-publishing/quora", tags=["social-publishing-quora"])

_service = QuoraUserAssistedService()


def _require_flag() -> None:
    if not getattr(_cfg, "ENABLE_HERMES_PROVIDER", False) or not getattr(
        _cfg, "ENABLE_HERMES_QUORA", False
    ):
        raise HTTPException(status_code=404, detail="Quora browser publishing is not enabled")


async def _operation_id_for(confirmation_id: str, tenant_id: str) -> Optional[str]:
    """Read-only lookup of a pending confirmation's operation_id (for media cleanup)."""
    try:
        pending = await _service._pending.find(confirmation_id, tenant_id)
    except Exception:
        return None
    return getattr(pending, "operation_id", None) if pending else None


# ── Request models ──────────────────────────────────────────────────────────

class CreateQuoraPostRequest(BaseModel):
    account_id: str = Field(..., min_length=1)
    # Post text. Optional only when an image is provided (validator enforces that
    # a post has text and/or one image).
    text: str = ""
    # Media is OPTIONAL (text-only posts allowed). At most one image. Supplied
    # either as app-content URLs (materialized) or local server paths.
    media_paths: Optional[list[str]] = None
    media_urls: Optional[list[str]] = None
    operation_id: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/posts")
async def create_quora_post(
    req: CreateQuoraPostRequest,
    user: dict = Depends(get_current_user),
):
    """
    Prepare a Quora post and STOP for user confirmation.

    Never publishes. Returns ACTION_REQUIRED with a preview + confirmation_id.
    """
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    operation_id = req.operation_id or f"quora_{uuid.uuid4().hex[:16]}"

    # Resolve media (optional) to LOCAL file paths. UI sends media_urls; dev
    # tooling may send media_paths directly.
    media_paths = req.media_paths or []
    materialized = False
    log.warning(f"QUORA_ROUTE op={operation_id} media_urls={len(req.media_urls or [])} "
                f"media_paths={len(media_paths)} first_url={(req.media_urls[0] if req.media_urls else None)!r}")
    if not media_paths and req.media_urls:
        try:
            media_paths = materialize_media_urls(req.media_urls, operation_id)
            materialized = True
            log.warning(f"QUORA_ROUTE op={operation_id} materialized={len(media_paths)} "
                        f"-> {(media_paths[0] if media_paths else None)!r}")
        except MediaMaterializeError as e:
            cleanup_operation_media(operation_id)
            log.warning("Quora media materialization failed",
                        op=operation_id, err=f"{type(e).__name__}: {e}")
            return {
                "status": "failed",
                "message": f"Could not prepare the Quora image: {e}",
            }

    result = await _service.prepare(
        tenant_id=tenant_id,
        account_id=req.account_id,
        operation_id=operation_id,
        text=req.text,
        media_paths=media_paths,
    )
    # If prepare did not reach ACTION_REQUIRED, no pending record persists the
    # files for a later confirm — clean up media we materialized from URLs.
    if materialized and result.get("status") != "action_required":
        cleanup_operation_media(operation_id)
    return result


@router.post("/confirm/{confirmation_id}")
async def confirm_quora_post(
    confirmation_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Explicit user confirmation → resume the SAME operation, post, verify.

    This is the ONLY path that clicks Quora's Post button.
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
async def cancel_quora_post(
    confirmation_id: str,
    user: dict = Depends(get_current_user),
):
    """Cancel a pending Quora post without publishing."""
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
async def list_pending_quora_posts(user: dict = Depends(get_current_user)):
    """List Quora posts awaiting the current tenant's confirmation."""
    _require_flag()
    tenant_id = resolve_tenant_id(user)
    return {"pending": await _service.list_pending(tenant_id)}
