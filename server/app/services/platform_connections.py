"""
Multi-platform connection service - manages OAuth connections for all platforms.
Extends the LinkedIn multi-account pattern to Twitter, Reddit, Medium, Quora.
"""

import os
from pathlib import Path
from datetime import datetime, timezone
from bson import ObjectId
from dotenv import load_dotenv
from app.database import db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

connected_accounts_collection = db["connected_accounts"]
MAX_ACCOUNTS_PER_PLATFORM = 3

# Platform OAuth configs from .env
PLATFORM_CONFIGS = {
    "twitter": {
        "client_id": os.getenv("TWITTER_CLIENT_ID", ""),
        "client_secret": os.getenv("TWITTER_CLIENT_SECRET", ""),
        "redirect_uri": os.getenv("TWITTER_REDIRECT_URI", "http://localhost:8000/auth/twitter/callback"),
        "auth_url": "https://twitter.com/i/oauth2/authorize",
        "token_url": "https://api.twitter.com/2/oauth2/token",
        "mode": "manual_assisted",
    },
    "reddit": {
        "client_id": os.getenv("REDDIT_CLIENT_ID", ""),
        "client_secret": os.getenv("REDDIT_CLIENT_SECRET", ""),
        "redirect_uri": os.getenv("REDDIT_REDIRECT_URI", "http://localhost:8000/auth/reddit/callback"),
        "auth_url": "https://www.reddit.com/api/v1/authorize",
        "token_url": "https://www.reddit.com/api/v1/access_token",
        "mode": "auto",
    },
    "medium": {
        "client_id": os.getenv("MEDIUM_CLIENT_ID", ""),
        "client_secret": os.getenv("MEDIUM_CLIENT_SECRET", ""),
        "redirect_uri": os.getenv("MEDIUM_REDIRECT_URI", "http://localhost:8000/auth/medium/callback"),
        "auth_url": "https://medium.com/m/oauth/authorize",
        "token_url": "https://api.medium.com/v1/tokens",
        "mode": "auto",
    },
    "quora": {
        "client_id": "",
        "client_secret": "",
        "redirect_uri": "",
        "auth_url": "",
        "token_url": "",
        "mode": "manual_assisted",
    },
}


def serialize_account(doc):
    """Serialize account document (never expose tokens)."""
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


# ============ GENERIC ACCOUNT MANAGEMENT ============ #

async def get_platform_accounts(user_id: str, platform: str) -> dict:
    """Get all connected accounts for a platform."""
    cursor = connected_accounts_collection.find({
        "user_id": user_id,
        "platform": platform,
    }).sort("connected_at", 1)
    docs = await cursor.to_list(length=MAX_ACCOUNTS_PER_PLATFORM)
    accounts = [serialize_account(doc) for doc in docs]
    return {
        "accounts": accounts,
        "count": len(accounts),
        "max": MAX_ACCOUNTS_PER_PLATFORM,
        "mode": PLATFORM_CONFIGS.get(platform, {}).get("mode", "manual_assisted"),
        "oauth_available": bool(PLATFORM_CONFIGS.get(platform, {}).get("client_id")),
    }


async def save_platform_connection(user_id: str, platform: str, token_data: dict, profile_data: dict):
    """Save a new platform account connection."""
    now = datetime.now(timezone.utc)
    platform_user_id = profile_data.get("id", profile_data.get("sub", ""))
    account_name = profile_data.get("name", profile_data.get("username", f"{platform} Account"))
    account_email = profile_data.get("email", "")

    # Check if already connected
    existing = await connected_accounts_collection.find_one({
        "user_id": user_id,
        "platform": platform,
        "platform_user_id": platform_user_id,
    })

    if existing:
        await connected_accounts_collection.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "access_token": token_data.get("access_token", ""),
                "refresh_token": token_data.get("refresh_token", ""),
                "token_expiry": token_data.get("expires_in", 0),
                "account_name": account_name,
                "account_email": account_email,
                "status": "active",
                "connected_at": now,
            }},
        )
        return

    # Check limit
    count = await connected_accounts_collection.count_documents({
        "user_id": user_id, "platform": platform,
    })
    if count >= MAX_ACCOUNTS_PER_PLATFORM:
        return

    is_default = count == 0

    await connected_accounts_collection.insert_one({
        "user_id": user_id,
        "platform": platform,
        "account_type": "personal",
        "account_name": account_name,
        "account_email": account_email,
        "platform_user_id": platform_user_id,
        "access_token": token_data.get("access_token", ""),
        "refresh_token": token_data.get("refresh_token", ""),
        "token_expiry": token_data.get("expires_in", 0),
        "is_default": is_default,
        "status": "active",
        "connected_at": now,
    })


async def set_default_platform_account(user_id: str, platform: str, account_id: str) -> dict:
    """Set default account for a platform."""
    account = await connected_accounts_collection.find_one({
        "_id": ObjectId(account_id), "user_id": user_id, "platform": platform,
    })
    if not account:
        return {"error": "Account not found"}

    await connected_accounts_collection.update_many(
        {"user_id": user_id, "platform": platform},
        {"$set": {"is_default": False}},
    )
    await connected_accounts_collection.update_one(
        {"_id": ObjectId(account_id)},
        {"$set": {"is_default": True}},
    )
    return {"message": f"Default set to {account.get('account_name')}"}


async def disconnect_platform_account(user_id: str, platform: str, account_id: str) -> dict:
    """Disconnect a specific platform account."""
    result = await connected_accounts_collection.delete_one({
        "_id": ObjectId(account_id), "user_id": user_id, "platform": platform,
    })
    if result.deleted_count == 0:
        return {"error": "Account not found"}

    # Reassign default
    remaining = await connected_accounts_collection.find_one({"user_id": user_id, "platform": platform})
    if remaining:
        await connected_accounts_collection.update_one({"_id": remaining["_id"]}, {"$set": {"is_default": True}})

    return {"message": "Account disconnected"}


async def rename_platform_account(user_id: str, platform: str, account_id: str, name: str) -> dict:
    """Rename a platform account."""
    result = await connected_accounts_collection.update_one(
        {"_id": ObjectId(account_id), "user_id": user_id, "platform": platform},
        {"$set": {"account_name": name}},
    )
    if result.modified_count == 0:
        return {"error": "Account not found"}
    return {"message": f"Renamed to {name}"}


async def get_active_token(user_id: str, platform: str, account_id: str = None) -> dict:
    """Get the active access token for publishing."""
    if account_id:
        doc = await connected_accounts_collection.find_one({
            "_id": ObjectId(account_id), "user_id": user_id, "platform": platform, "status": "active",
        })
    else:
        doc = await connected_accounts_collection.find_one({
            "user_id": user_id, "platform": platform, "is_default": True, "status": "active",
        })
        if not doc:
            doc = await connected_accounts_collection.find_one({
                "user_id": user_id, "platform": platform, "status": "active",
            })

    if not doc:
        return None
    return {
        "access_token": doc.get("access_token", ""),
        "refresh_token": doc.get("refresh_token", ""),
        "platform_user_id": doc.get("platform_user_id", ""),
        "account_name": doc.get("account_name", ""),
    }


async def get_all_connections_status(user_id: str) -> dict:
    """Get connection status for all platforms."""
    from app.services.linkedin_service import get_linkedin_connection, LINKEDIN_CLIENT_ID

    linkedin = await get_linkedin_connection(user_id)

    platforms = {}
    for platform in ["linkedin", "twitter", "reddit", "medium", "quora", "instagram", "meta"]:
        if platform == "linkedin":
            platforms[platform] = {
                "connected": linkedin is not None,
                "details": linkedin,
                "oauth_available": bool(LINKEDIN_CLIENT_ID),
                "mode": "auto",
            }
        else:
            data = await get_platform_accounts(user_id, platform)
            platforms[platform] = {
                "connected": data["count"] > 0,
                "details": data if data["count"] > 0 else None,
                "oauth_available": data["oauth_available"],
                "mode": data["mode"],
            }

    return platforms
