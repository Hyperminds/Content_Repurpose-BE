"""API routes for social account OAuth connection flows.

Endpoints:
  POST /social-publishing/connect/{platform}  — Initiate OAuth flow
  GET  /social-publishing/callback/{platform} — OAuth callback (platform redirect)
  POST /social-publishing/accounts/{id}/disconnect — Disconnect account
  GET  /social-publishing/platforms — List supported platforms with status
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Optional

from app.config import FRONTEND_URL
from app.utils.jwt_handler import get_current_user
from app.core.identity import org_id_from_user, user_id_from_user
from app.social_publishing.api.dependencies import resolve_tenant_id
from app.social_publishing.domain.exceptions import (
    SocialPublishingError,
    PlatformNotSupported,
    ValidationError,
)
from app.social_publishing.services.account_connection_service import AccountConnectionService
from app.social_publishing.auth.provider import AuthProviderRegistry
from app.social_publishing.auth.oauth_state_store import OAuthStateStore
from app.social_publishing.auth.linkedin_provider import LinkedInAuthProvider
from app.social_publishing.credentials.vault import CredentialVault
from app.social_publishing.repositories.social_accounts_repository import SocialAccountsRepository

router = APIRouter(prefix="/social-publishing", tags=["social-publishing-oauth"])

# ── Singleton dependencies ────────────────────────────────────────────────────

_accounts_repo = SocialAccountsRepository()
_state_store = OAuthStateStore()
_vault = CredentialVault()
_auth_registry = AuthProviderRegistry()

# Register available providers
_auth_registry.register(LinkedInAuthProvider())

from app.social_publishing.integrations.facebook import FacebookAuthProvider
_auth_registry.register(FacebookAuthProvider())

from app.social_publishing.integrations.instagram import InstagramAuthProvider
_auth_registry.register(InstagramAuthProvider())

from app.social_publishing.integrations.twitter import TwitterAuthProvider
_auth_registry.register(TwitterAuthProvider())

_connection_service = AccountConnectionService(
    accounts_repo=_accounts_repo,
    auth_registry=_auth_registry,
    state_store=_state_store,
    vault=_vault,
)


def _get_connection_service() -> AccountConnectionService:
    return _connection_service


# ── Exception mapping ─────────────────────────────────────────────────────────

_STATUS_MAP = {
    PlatformNotSupported: 400,
    ValidationError: 422,
}


def _raise_http(exc: SocialPublishingError) -> None:
    status = _STATUS_MAP.get(type(exc), 400)
    raise HTTPException(status_code=status, detail=exc.message)


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/connect/{platform}")
async def initiate_connection(
    platform: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Initiate OAuth connection flow for a platform.

    Returns the authorization URL the frontend should redirect the user to.
    """
    tenant_id = resolve_tenant_id(user)
    user_id = user_id_from_user(user) or tenant_id

    # Build callback URL for this platform
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/social-publishing/callback/{platform}"

    svc = _get_connection_service()
    try:
        result = await svc.initiate_connection(
            tenant_id=tenant_id,
            user_id=user_id,
            platform=platform,
            redirect_uri=redirect_uri,
        )
    except SocialPublishingError as e:
        _raise_http(e)

    return result


@router.get("/callback/{platform}")
async def oauth_callback(
    platform: str,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    error_description: Optional[str] = Query(None),
):
    """
    OAuth callback endpoint — called by the platform after user authorization.

    On success: completes the connection and redirects to frontend with success.
    On failure: redirects to frontend with error.

    NOTE: This endpoint does NOT require Bearer auth because it's called by the
    platform's redirect — the user's identity is verified via the state token.
    """
    from fastapi.responses import RedirectResponse

    frontend_settings = f"{FRONTEND_URL}/social-accounts"

    # Handle platform-reported errors
    if error:
        error_msg = error_description or error
        return RedirectResponse(
            url=f"{frontend_settings}?connection=failed&error={error_msg}",
            status_code=302,
        )

    # Validate required params
    if not code or not state:
        return RedirectResponse(
            url=f"{frontend_settings}?connection=failed&error=missing_parameters",
            status_code=302,
        )

    # Complete the connection
    svc = _get_connection_service()
    try:
        account = await svc.complete_connection(
            state_token=state,
            authorization_code=code,
        )
    except SocialPublishingError as e:
        return RedirectResponse(
            url=f"{frontend_settings}?connection=failed&error=connection_failed",
            status_code=302,
        )

    # Success — redirect to frontend
    return RedirectResponse(
        url=f"{frontend_settings}?connection=success&platform={platform}&account_id={account.id}",
        status_code=302,
    )


@router.post("/accounts/{account_id}/disconnect")
async def disconnect_account(
    account_id: str,
    user: dict = Depends(get_current_user),
):
    """Disconnect a social account — clears credentials."""
    tenant_id = resolve_tenant_id(user)
    svc = _get_connection_service()

    success = await svc.disconnect_account(account_id, tenant_id)
    if not success:
        raise HTTPException(status_code=404, detail="Account not found")

    return {"message": "Account disconnected", "account_id": account_id}


@router.get("/platforms")
async def list_supported_platforms(user: dict = Depends(get_current_user)):
    """List all platforms and their OAuth connection availability."""
    from app.social_publishing.domain.enums import SocialPlatform

    platforms = []
    for p in SocialPlatform:
        has_provider = _auth_registry.has(p)
        platforms.append({
            "platform": p.value,
            "oauth_supported": has_provider,
            "status": "supported" if has_provider else "coming_soon",
        })

    return {"platforms": platforms}
