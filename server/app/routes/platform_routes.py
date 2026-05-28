"""
Multi-platform OAuth and account management routes.
Handles: Twitter, Reddit, Medium, Quora connections.
"""

import os
import httpx
from fastapi import APIRouter, Query, Depends, HTTPException, Body
from fastapi.responses import RedirectResponse
from app.utils.jwt_handler import get_current_user
from app.services.platform_connections import (
    get_platform_accounts,
    save_platform_connection,
    set_default_platform_account,
    disconnect_platform_account,
    rename_platform_account,
    get_all_connections_status,
    PLATFORM_CONFIGS,
    MAX_ACCOUNTS_PER_PLATFORM,
)

router = APIRouter(prefix="/platforms", tags=["platform-connections"])

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")


# ============ GENERIC PLATFORM ROUTES ============ #

@router.get("/{platform}/accounts")
async def list_accounts(platform: str, user: dict = Depends(get_current_user)):
    """Get all connected accounts for a platform."""
    return await get_platform_accounts(user["user_id"], platform)


@router.get("/{platform}/connect")
async def connect_platform(platform: str, user: dict = Depends(get_current_user)):
    """Start OAuth flow for a platform."""
    config = PLATFORM_CONFIGS.get(platform)
    if not config or not config["client_id"]:
        raise HTTPException(status_code=400, detail=f"{platform} OAuth not configured.")

    # Check account limit
    data = await get_platform_accounts(user["user_id"], platform)
    if data["count"] >= MAX_ACCOUNTS_PER_PLATFORM:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_ACCOUNTS_PER_PLATFORM} accounts allowed.")

    # Build OAuth URL based on platform
    state = f"{user['user_id']}:{platform}"

    if platform == "twitter":
        scopes = "tweet.read tweet.write users.read offline.access"
        auth_url = (
            f"{config['auth_url']}?response_type=code"
            f"&client_id={config['client_id']}"
            f"&redirect_uri={config['redirect_uri']}"
            f"&scope={scopes}"
            f"&state={state}"
            f"&code_challenge=challenge&code_challenge_method=plain"
        )
    elif platform == "reddit":
        scopes = "identity submit read"
        auth_url = (
            f"{config['auth_url']}?client_id={config['client_id']}"
            f"&response_type=code"
            f"&state={state}"
            f"&redirect_uri={config['redirect_uri']}"
            f"&duration=permanent"
            f"&scope={scopes}"
        )
    elif platform == "medium":
        scopes = "basicProfile,publishPost"
        auth_url = (
            f"{config['auth_url']}?client_id={config['client_id']}"
            f"&scope={scopes}"
            f"&state={state}"
            f"&response_type=code"
            f"&redirect_uri={config['redirect_uri']}"
        )
    else:
        raise HTTPException(status_code=400, detail=f"OAuth not supported for {platform}")

    return {"auth_url": auth_url}


@router.get("/{platform}/callback")
async def platform_callback(
    platform: str,
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    """OAuth callback for any platform."""
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/platforms?error={platform}_denied")
    if not code or not state:
        return RedirectResponse(f"{FRONTEND_URL}/platforms?error={platform}_invalid")

    # Parse state
    parts = state.split(":")
    user_id = parts[0] if parts else ""

    config = PLATFORM_CONFIGS.get(platform)
    if not config:
        return RedirectResponse(f"{FRONTEND_URL}/platforms?error=unknown_platform")

    # Exchange code for token
    token_data = await _exchange_token(platform, code, config)
    if "error" in token_data:
        return RedirectResponse(f"{FRONTEND_URL}/platforms?error={platform}_token_failed")

    # Get profile
    profile_data = await _get_profile(platform, token_data.get("access_token", ""))
    if "error" in profile_data:
        return RedirectResponse(f"{FRONTEND_URL}/platforms?error={platform}_profile_failed")

    # Save connection
    await save_platform_connection(user_id, platform, token_data, profile_data)

    return RedirectResponse(f"{FRONTEND_URL}/platforms?connected={platform}")


@router.put("/{platform}/accounts/{account_id}/default")
async def set_default(platform: str, account_id: str, user: dict = Depends(get_current_user)):
    """Set default account."""
    result = await set_default_platform_account(user["user_id"], platform, account_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.put("/{platform}/accounts/{account_id}/rename")
async def rename(platform: str, account_id: str, data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Rename account."""
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    result = await rename_platform_account(user["user_id"], platform, account_id, name)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/{platform}/accounts/{account_id}")
async def disconnect(platform: str, account_id: str, user: dict = Depends(get_current_user)):
    """Disconnect account."""
    result = await disconnect_platform_account(user["user_id"], platform, account_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/connections")
async def all_connections(user: dict = Depends(get_current_user)):
    """Get all platform connection statuses."""
    return await get_all_connections_status(user["user_id"])


# ============ TOKEN EXCHANGE HELPERS ============ #

async def _exchange_token(platform: str, code: str, config: dict) -> dict:
    """Exchange auth code for access token."""
    async with httpx.AsyncClient() as client:
        if platform == "twitter":
            response = await client.post(
                config["token_url"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": config["redirect_uri"],
                    "code_verifier": "challenge",
                },
                auth=(config["client_id"], config["client_secret"]),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        elif platform == "reddit":
            response = await client.post(
                config["token_url"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": config["redirect_uri"],
                },
                auth=(config["client_id"], config["client_secret"]),
                headers={"User-Agent": "ContentRepurposer/1.0"},
            )
        elif platform == "medium":
            response = await client.post(
                config["token_url"],
                data={
                    "code": code,
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                    "grant_type": "authorization_code",
                    "redirect_uri": config["redirect_uri"],
                },
            )
        else:
            return {"error": "Unsupported platform"}

        if response.status_code != 200:
            return {"error": f"Token exchange failed: {response.text[:200]}"}
        return response.json()


async def _get_profile(platform: str, access_token: str) -> dict:
    """Get user profile from platform API."""
    async with httpx.AsyncClient() as client:
        if platform == "twitter":
            response = await client.get(
                "https://api.twitter.com/2/users/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code == 200:
                data = response.json().get("data", {})
                return {"id": data.get("id"), "name": data.get("name"), "username": data.get("username")}
        elif platform == "reddit":
            response = await client.get(
                "https://oauth.reddit.com/api/v1/me",
                headers={"Authorization": f"Bearer {access_token}", "User-Agent": "ContentRepurposer/1.0"},
            )
            if response.status_code == 200:
                data = response.json()
                return {"id": data.get("id"), "name": data.get("name"), "username": data.get("name")}
        elif platform == "medium":
            response = await client.get(
                "https://api.medium.com/v1/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status_code == 200:
                data = response.json().get("data", {})
                return {"id": data.get("id"), "name": data.get("name"), "username": data.get("username")}

        return {"error": "Failed to get profile"}
