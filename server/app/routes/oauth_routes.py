"""
OAuth routes for platform connections (LinkedIn multi-account support).
"""

from fastapi import APIRouter, Query, Depends, HTTPException, Body
from fastapi.responses import RedirectResponse
from app.utils.jwt_handler import get_current_user
from app.services.linkedin_service import (
    get_linkedin_auth_url,
    exchange_code_for_token,
    get_linkedin_profile,
    save_linkedin_connection,
    get_linkedin_connection,
    get_linkedin_accounts,
    set_default_account,
    rename_account,
    set_account_type,
    disconnect_account,
    disconnect_linkedin,
    set_organization_id,
    FRONTEND_URL,
    LINKEDIN_CLIENT_ID,
    MAX_ACCOUNTS_PER_PLATFORM,
)

router = APIRouter(prefix="/auth", tags=["oauth"])


# ============ LINKEDIN OAUTH ============ #

@router.get("/linkedin/connect")
async def linkedin_connect(user: dict = Depends(get_current_user)):
    """Start LinkedIn OAuth flow. Supports adding multiple accounts."""
    if not LINKEDIN_CLIENT_ID:
        raise HTTPException(status_code=400, detail="LinkedIn OAuth not configured.")

    # Check account limit
    accounts = await get_linkedin_accounts(user["user_id"])
    if len(accounts) >= MAX_ACCOUNTS_PER_PLATFORM:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_ACCOUNTS_PER_PLATFORM} LinkedIn accounts allowed.")

    auth_url = get_linkedin_auth_url(user["user_id"])
    return {"auth_url": auth_url}


@router.get("/linkedin/callback")
async def linkedin_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    """LinkedIn OAuth callback - exchanges code for token and saves as new account."""
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/platforms?error=linkedin_denied")
    if not code or not state:
        return RedirectResponse(f"{FRONTEND_URL}/platforms?error=linkedin_invalid")

    user_id = state

    token_data = await exchange_code_for_token(code)
    if "error" in token_data:
        return RedirectResponse(f"{FRONTEND_URL}/platforms?error=linkedin_token_failed")

    access_token = token_data.get("access_token", "")
    profile_data = await get_linkedin_profile(access_token)
    if "error" in profile_data:
        return RedirectResponse(f"{FRONTEND_URL}/platforms?error=linkedin_profile_failed")

    await save_linkedin_connection(user_id, token_data, profile_data)

    return RedirectResponse(f"{FRONTEND_URL}/platforms?connected=linkedin")


# ============ ACCOUNT MANAGEMENT ============ #

@router.get("/linkedin/accounts")
async def list_linkedin_accounts(user: dict = Depends(get_current_user)):
    """Get all connected LinkedIn accounts."""
    accounts = await get_linkedin_accounts(user["user_id"])
    return {
        "accounts": accounts,
        "count": len(accounts),
        "max": MAX_ACCOUNTS_PER_PLATFORM,
    }


@router.get("/linkedin/status")
async def linkedin_status(user: dict = Depends(get_current_user)):
    """Check LinkedIn connection status (backward compatible)."""
    connection = await get_linkedin_connection(user["user_id"])
    if not connection:
        return {"connected": False, "platform": "linkedin"}
    return {"connected": True, **connection}


@router.put("/linkedin/accounts/{account_id}/default")
async def set_default(account_id: str, user: dict = Depends(get_current_user)):
    """Set a specific account as the default posting account."""
    result = await set_default_account(user["user_id"], account_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.put("/linkedin/accounts/{account_id}/rename")
async def rename(account_id: str, data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Rename a connected account."""
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    result = await rename_account(user["user_id"], account_id, name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.put("/linkedin/accounts/{account_id}/type")
async def set_type(account_id: str, data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Set account type (personal/business)."""
    result = await set_account_type(user["user_id"], account_id, data.get("account_type", ""))
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/linkedin/accounts/{account_id}")
async def remove_account(account_id: str, user: dict = Depends(get_current_user)):
    """Disconnect a specific LinkedIn account."""
    result = await disconnect_account(user["user_id"], account_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/linkedin/disconnect")
async def linkedin_disconnect(user: dict = Depends(get_current_user)):
    """Disconnect ALL LinkedIn accounts."""
    result = await disconnect_linkedin(user["user_id"])
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.put("/linkedin/organization")
async def set_linkedin_org(data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Set organization ID for company page posting on default account."""
    org_id = data.get("organization_id", "").strip()
    if not org_id:
        raise HTTPException(status_code=400, detail="organization_id is required")
    result = await set_organization_id(user["user_id"], org_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ============ ALL PLATFORM CONNECTIONS ============ #

@router.get("/connections")
async def get_all_connections(user: dict = Depends(get_current_user)):
    """Get all connected platform statuses."""
    linkedin = await get_linkedin_connection(user["user_id"])

    platforms = {
        "linkedin": {
            "connected": linkedin is not None,
            "details": linkedin,
            "oauth_available": bool(LINKEDIN_CLIENT_ID),
        },
        "instagram": {"connected": False, "details": None, "oauth_available": False},
        "twitter": {"connected": False, "details": None, "oauth_available": False},
        "reddit": {"connected": False, "details": None, "oauth_available": False},
        "medium": {"connected": False, "details": None, "oauth_available": False},
        "meta": {"connected": False, "details": None, "oauth_available": False},
        "quora": {"connected": False, "details": None, "oauth_available": False},
    }

    return platforms
