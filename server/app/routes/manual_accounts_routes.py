"""
Manual account management for platforms that don't need OAuth (Twitter/X, Quora).
Users simply save their username/profile URL to track which account they post to.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Body, Depends, HTTPException
from bson import ObjectId
from app.utils.jwt_handler import get_current_user
from app.database import db

router = APIRouter(prefix="/manual-accounts", tags=["manual-accounts"])

connected_accounts_collection = db["connected_accounts"]
MAX_ACCOUNTS = 3


@router.post("/{platform}")
async def add_manual_account(platform: str, data: dict = Body(...), user: dict = Depends(get_current_user)):
    """Add a manual account (just username/profile info, no OAuth)."""
    if platform not in ("twitter", "quora", "instagram", "reddit", "medium", "meta"):
        raise HTTPException(status_code=400, detail="Platform not supported for manual accounts.")

    username = data.get("username", "").strip().lstrip("@")
    display_name = data.get("display_name", "").strip() or username

    if not username:
        raise HTTPException(status_code=400, detail="Username is required.")

    # Check limit
    count = await connected_accounts_collection.count_documents({
        "user_id": user["user_id"], "platform": platform,
    })
    if count >= MAX_ACCOUNTS:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_ACCOUNTS} accounts allowed.")

    # Check if already added
    existing = await connected_accounts_collection.find_one({
        "user_id": user["user_id"], "platform": platform, "platform_user_id": username,
    })
    if existing:
        raise HTTPException(status_code=400, detail="This account is already connected.")

    is_default = count == 0

    doc = {
        "user_id": user["user_id"],
        "platform": platform,
        "account_type": "personal",
        "account_name": display_name,
        "account_email": "",
        "platform_user_id": username,
        "access_token": "",
        "refresh_token": "",
        "token_expiry": 0,
        "is_default": is_default,
        "status": "active",
        "connection_type": "manual",
        "connected_at": datetime.now(timezone.utc),
    }

    result = await connected_accounts_collection.insert_one(doc)

    return {
        "id": str(result.inserted_id),
        "platform": platform,
        "account_name": display_name,
        "username": username,
        "is_default": is_default,
        "message": f"@{username} added to {platform}",
    }


@router.get("/{platform}")
async def list_manual_accounts(platform: str, user: dict = Depends(get_current_user)):
    """List manual accounts for a platform."""
    cursor = connected_accounts_collection.find({
        "user_id": user["user_id"], "platform": platform,
    }).sort("connected_at", 1)
    docs = await cursor.to_list(length=MAX_ACCOUNTS)

    return {
        "accounts": [
            {
                "id": str(d["_id"]),
                "account_name": d.get("account_name", ""),
                "username": d.get("platform_user_id", ""),
                "is_default": d.get("is_default", False),
                "status": d.get("status", "active"),
                "connected_at": d.get("connected_at").isoformat() if d.get("connected_at") else None,
            }
            for d in docs
        ],
        "count": len(docs),
        "max": MAX_ACCOUNTS,
    }


@router.delete("/{platform}/{account_id}")
async def remove_manual_account(platform: str, account_id: str, user: dict = Depends(get_current_user)):
    """Remove a manual account."""
    result = await connected_accounts_collection.delete_one({
        "_id": ObjectId(account_id), "user_id": user["user_id"], "platform": platform,
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Account not found")

    # Reassign default
    remaining = await connected_accounts_collection.find_one({"user_id": user["user_id"], "platform": platform})
    if remaining:
        await connected_accounts_collection.update_one({"_id": remaining["_id"]}, {"$set": {"is_default": True}})

    return {"message": "Account removed"}


@router.put("/{platform}/{account_id}/default")
async def set_default_manual(platform: str, account_id: str, user: dict = Depends(get_current_user)):
    """Set default manual account."""
    await connected_accounts_collection.update_many(
        {"user_id": user["user_id"], "platform": platform},
        {"$set": {"is_default": False}},
    )
    await connected_accounts_collection.update_one(
        {"_id": ObjectId(account_id), "user_id": user["user_id"]},
        {"$set": {"is_default": True}},
    )
    return {"message": "Default account updated"}
