"""
LinkedIn OAuth 2.0 + Multi-Account Publishing Service.
Supports multiple connected accounts per user (max 3).
"""

import os
import httpx
from pathlib import Path
from datetime import datetime, timezone
from bson import ObjectId
from dotenv import load_dotenv
from app.database import db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
LINKEDIN_REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/auth/linkedin/callback")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# LinkedIn API endpoints
LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_POST_URL = "https://api.linkedin.com/v2/ugcPosts"

# Collection for storing connected accounts
connected_accounts_collection = db["connected_accounts"]

MAX_ACCOUNTS_PER_PLATFORM = 3


# ============ SERIALIZATION ============ #

def serialize_account(doc):
    """Convert MongoDB doc to JSON-safe dict (never expose tokens)."""
    return {
        "id": str(doc["_id"]),
        "user_id": doc.get("user_id"),
        "platform": doc.get("platform"),
        "account_type": doc.get("account_type", "personal"),
        "account_name": doc.get("account_name", ""),
        "account_email": doc.get("account_email", ""),
        "platform_user_id": doc.get("platform_user_id", ""),
        "is_default": doc.get("is_default", False),
        "status": doc.get("status", "active"),
        "connected_at": doc.get("connected_at").isoformat() if doc.get("connected_at") else None,
    }


# ============ OAUTH FLOW ============ #

def get_linkedin_auth_url(user_id: str) -> str:
    """Generate LinkedIn OAuth authorization URL."""
    if not LINKEDIN_CLIENT_ID:
        return ""

    scopes = "openid profile email w_member_social"
    state = user_id

    return (
        f"{LINKEDIN_AUTH_URL}"
        f"?response_type=code"
        f"&client_id={LINKEDIN_CLIENT_ID}"
        f"&redirect_uri={LINKEDIN_REDIRECT_URI}"
        f"&state={state}"
        f"&scope={scopes}"
    )


async def exchange_code_for_token(code: str) -> dict:
    """Exchange authorization code for access token."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            LINKEDIN_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": LINKEDIN_REDIRECT_URI,
                "client_id": LINKEDIN_CLIENT_ID,
                "client_secret": LINKEDIN_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            return {"error": f"Token exchange failed: {response.text}"}
        return response.json()


async def get_linkedin_profile(access_token: str) -> dict:
    """Get LinkedIn user profile using the access token."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            LINKEDIN_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code != 200:
            return {"error": f"Failed to get profile: {response.text}"}
        return response.json()


# ============ MULTI-ACCOUNT MANAGEMENT ============ #

async def save_linkedin_connection(user_id: str, token_data: dict, profile_data: dict):
    """
    Save a new LinkedIn account connection. Supports multiple accounts.
    If the same LinkedIn account is already connected, update it instead.
    """
    now = datetime.now(timezone.utc)
    platform_user_id = profile_data.get("sub", "")
    account_name = profile_data.get("name", "LinkedIn Account")
    account_email = profile_data.get("email", "")

    # Check if this specific LinkedIn account is already connected
    existing = await connected_accounts_collection.find_one({
        "user_id": user_id,
        "platform": "linkedin",
        "platform_user_id": platform_user_id,
    })

    if existing:
        # Update existing connection (reconnect)
        await connected_accounts_collection.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "access_token": token_data.get("access_token", ""),
                "refresh_token": token_data.get("refresh_token", ""),
                "expires_in": token_data.get("expires_in", 0),
                "account_name": account_name,
                "account_email": account_email,
                "status": "active",
                "connected_at": now,
            }},
        )
        return

    # Check account limit
    count = await connected_accounts_collection.count_documents({
        "user_id": user_id,
        "platform": "linkedin",
    })
    if count >= MAX_ACCOUNTS_PER_PLATFORM:
        return  # Silently skip — frontend should check limit before allowing

    # Determine if this should be default (first account = default)
    is_default = count == 0

    # Insert new account
    await connected_accounts_collection.insert_one({
        "user_id": user_id,
        "platform": "linkedin",
        "account_type": "personal",
        "account_name": account_name,
        "account_email": account_email,
        "platform_user_id": platform_user_id,
        "access_token": token_data.get("access_token", ""),
        "refresh_token": token_data.get("refresh_token", ""),
        "expires_in": token_data.get("expires_in", 0),
        "organization_id": "",
        "is_default": is_default,
        "status": "active",
        "connected_at": now,
    })


async def get_linkedin_accounts(user_id: str) -> list:
    """Get all connected LinkedIn accounts for a user."""
    cursor = connected_accounts_collection.find({
        "user_id": user_id,
        "platform": "linkedin",
    }).sort("connected_at", 1)
    docs = await cursor.to_list(length=MAX_ACCOUNTS_PER_PLATFORM)
    return [serialize_account(doc) for doc in docs]


async def get_linkedin_connection(user_id: str) -> dict:
    """Get the default LinkedIn connection (backward compatible)."""
    accounts = await get_linkedin_accounts(user_id)
    if not accounts:
        return None
    # Return the default one
    default = next((a for a in accounts if a["is_default"]), accounts[0])
    return {
        "platform": "linkedin",
        "linkedin_name": default["account_name"],
        "linkedin_email": default["account_email"],
        "status": default["status"],
        "connected_at": default["connected_at"],
        "accounts": accounts,
        "count": len(accounts),
        "max": MAX_ACCOUNTS_PER_PLATFORM,
    }


async def set_default_account(user_id: str, account_id: str) -> dict:
    """Set a specific account as the default posting account."""
    # Verify ownership
    account = await connected_accounts_collection.find_one({
        "_id": ObjectId(account_id),
        "user_id": user_id,
        "platform": "linkedin",
    })
    if not account:
        return {"error": "Account not found"}

    # Unset all defaults for this platform
    await connected_accounts_collection.update_many(
        {"user_id": user_id, "platform": "linkedin"},
        {"$set": {"is_default": False}},
    )
    # Set the selected one as default
    await connected_accounts_collection.update_one(
        {"_id": ObjectId(account_id)},
        {"$set": {"is_default": True}},
    )
    return {"message": f"Default account set to {account.get('account_name')}"}


async def rename_account(user_id: str, account_id: str, new_name: str) -> dict:
    """Rename a connected account locally."""
    result = await connected_accounts_collection.update_one(
        {"_id": ObjectId(account_id), "user_id": user_id, "platform": "linkedin"},
        {"$set": {"account_name": new_name}},
    )
    if result.modified_count == 0:
        return {"error": "Account not found"}
    return {"message": f"Account renamed to {new_name}"}


async def set_account_type(user_id: str, account_id: str, account_type: str) -> dict:
    """Set account type (personal/business)."""
    if account_type not in ("personal", "business"):
        return {"error": "Invalid account type. Use 'personal' or 'business'."}
    result = await connected_accounts_collection.update_one(
        {"_id": ObjectId(account_id), "user_id": user_id, "platform": "linkedin"},
        {"$set": {"account_type": account_type}},
    )
    if result.modified_count == 0:
        return {"error": "Account not found"}
    return {"message": f"Account type set to {account_type}"}


async def disconnect_account(user_id: str, account_id: str) -> dict:
    """Disconnect a specific LinkedIn account."""
    result = await connected_accounts_collection.delete_one({
        "_id": ObjectId(account_id),
        "user_id": user_id,
        "platform": "linkedin",
    })
    if result.deleted_count == 0:
        return {"error": "Account not found"}

    # If we deleted the default, make the first remaining one default
    remaining = await connected_accounts_collection.find_one({
        "user_id": user_id,
        "platform": "linkedin",
    })
    if remaining:
        await connected_accounts_collection.update_one(
            {"_id": remaining["_id"]},
            {"$set": {"is_default": True}},
        )

    return {"message": "Account disconnected"}


async def disconnect_linkedin(user_id: str) -> dict:
    """Disconnect ALL LinkedIn accounts (backward compatible)."""
    result = await connected_accounts_collection.delete_many({
        "user_id": user_id,
        "platform": "linkedin",
    })
    if result.deleted_count == 0:
        return {"error": "No LinkedIn connection found"}
    return {"message": "LinkedIn disconnected successfully"}


async def set_organization_id(user_id: str, org_id: str) -> dict:
    """Set organization ID on the default account."""
    result = await connected_accounts_collection.update_one(
        {"user_id": user_id, "platform": "linkedin", "is_default": True},
        {"$set": {"organization_id": org_id}},
    )
    if result.modified_count == 0:
        return {"error": "No default LinkedIn account found"}
    return {"message": f"Organization ID set to {org_id}"}


# ============ PUBLISHING ============ #

async def publish_to_linkedin(user_id: str, content: str, media_urls: list = None, account_id: str = None) -> dict:
    """
    Publish to LinkedIn using the specified account or the default one.
    """
    # Get the account to post from
    if account_id:
        connection = await connected_accounts_collection.find_one({
            "_id": ObjectId(account_id),
            "user_id": user_id,
            "platform": "linkedin",
            "status": "active",
        })
    else:
        # Use default account
        connection = await connected_accounts_collection.find_one({
            "user_id": user_id,
            "platform": "linkedin",
            "is_default": True,
            "status": "active",
        })
        # Fallback: any active account
        if not connection:
            connection = await connected_accounts_collection.find_one({
                "user_id": user_id,
                "platform": "linkedin",
                "status": "active",
            })

    if not connection:
        return {
            "success": False,
            "error": "LinkedIn not connected. Please connect your LinkedIn account first.",
            "needs_connection": True,
        }

    access_token = connection.get("access_token", "")
    linkedin_sub = connection.get("platform_user_id", "")
    org_id = connection.get("organization_id", "")

    if not access_token or not linkedin_sub:
        return {
            "success": False,
            "error": "LinkedIn connection is invalid. Please reconnect.",
            "needs_connection": True,
        }

    # Determine author
    if org_id:
        author = f"urn:li:organization:{org_id}"
    else:
        author = f"urn:li:person:{linkedin_sub}"

    # Handle image upload
    media_assets = []
    if media_urls:
        for image_url in media_urls:
            if image_url:
                asset = await _upload_image_to_linkedin(access_token, author, image_url)
                if asset:
                    media_assets.append(asset)

    # Build post payload
    if media_assets:
        media_content = [{"status": "READY", "media": asset} for asset in media_assets]
        post_body = {
            "author": author,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": content},
                    "shareMediaCategory": "IMAGE",
                    "media": media_content,
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
    else:
        post_body = {
            "author": author,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": content},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            LINKEDIN_POST_URL,
            json=post_body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )

        if response.status_code in (200, 201):
            post_id = response.headers.get("x-restli-id", "")
            if not post_id:
                try:
                    post_id = response.json().get("id", "")
                except Exception:
                    post_id = "unknown"
            return {"success": True, "platform_post_id": post_id}
        elif response.status_code == 401:
            await connected_accounts_collection.update_one(
                {"_id": connection["_id"]},
                {"$set": {"status": "expired"}},
            )
            return {
                "success": False,
                "error": "LinkedIn token expired. Please reconnect this account.",
                "needs_connection": True,
            }
        else:
            return {
                "success": False,
                "error": f"LinkedIn API error ({response.status_code}): {response.text}",
            }


async def _upload_image_to_linkedin(access_token: str, author: str, image_url: str) -> str:
    """Upload an image to LinkedIn and return the asset URN."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            register_body = {
                "registerUploadRequest": {
                    "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                    "owner": author,
                    "serviceRelationships": [
                        {"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}
                    ],
                }
            }

            reg_response = await client.post(
                "https://api.linkedin.com/v2/assets?action=registerUpload",
                json=register_body,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            )

            if reg_response.status_code not in (200, 201):
                return None

            reg_data = reg_response.json()
            upload_url = reg_data["value"]["uploadMechanism"][
                "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
            ]["uploadUrl"]
            asset = reg_data["value"]["asset"]

            img_response = await client.get(image_url, follow_redirects=True)
            if img_response.status_code != 200 or len(img_response.content) < 1000:
                return None

            upload_response = await client.put(
                upload_url,
                content=img_response.content,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/octet-stream"},
            )

            return asset if upload_response.status_code in (200, 201) else None

    except Exception as e:
        print(f"[LinkedIn] Image upload error: {e}")
        return None
