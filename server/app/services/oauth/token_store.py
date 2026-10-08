"""
Token store — unified access to OAuth tokens stored in `connected_accounts`.

This module extracts the token-access pattern that was previously inline in
linkedin_service.py and platform_connections.py into a reusable layer. Any
platform publisher calls `get_valid_token(user_id, platform)` and gets back
a token dict or None.

Responsibilities:
  - Read the active token for a (user_id, platform) pair
  - Detect token expiry (token_expires_at < now + buffer)
  - Mark accounts as expired when tokens cannot be refreshed
  - Provide a hook for platform-specific refresh logic (Phase 2/3)

Does NOT implement platform-specific refresh flows — those live in
per-platform oauth modules.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId

from app.database import db
from app.services.logger import log

connected_accounts_collection = db["connected_accounts"]

# How far before actual expiry we consider a token "expiring soon"
_EXPIRY_BUFFER = timedelta(minutes=10)


# ── Public API ────────────────────────────────────────────────────────────────

async def get_valid_token(
    user_id: str,
    platform: str,
    account_id: Optional[str] = None,
) -> Optional[dict]:
    """
    Retrieve a valid access token for publishing.

    Returns a dict with keys:
        access_token, refresh_token, platform_user_id, account_name, account_id

    Returns None if no active account exists or all tokens are expired.

    Lookup order:
      1. Specific account_id (if provided)
      2. Default account (is_default=True)
      3. Any active account for the platform
    """
    doc = await _find_account(user_id, platform, account_id)
    if not doc:
        return None

    # Check if token is expired or about to expire
    if _is_token_expired(doc):
        log.warning(
            f"Token expired for {platform}",
            user_id=user_id,
            account_id=str(doc["_id"]),
        )
        await mark_account_expired(str(doc["_id"]))
        return None

    return _serialize_token(doc)


async def get_all_platform_tokens(
    user_id: str,
    platform: str,
) -> list[dict]:
    """Get all active tokens for a user+platform (for batch operations)."""
    cursor = connected_accounts_collection.find({
        "user_id": user_id,
        "platform": platform,
        "status": "active",
    })
    docs = await cursor.to_list(length=10)
    return [_serialize_token(doc) for doc in docs if not _is_token_expired(doc)]


async def store_token(
    user_id: str,
    platform: str,
    platform_user_id: str,
    access_token: str,
    refresh_token: str = "",
    expires_in_seconds: int = 0,
    account_name: str = "",
    account_email: str = "",
) -> str:
    """
    Store or update a token. Returns the account_id.

    If an account with the same (user_id, platform, platform_user_id) exists,
    it is updated. Otherwise a new account is created.
    """
    now = datetime.now(timezone.utc)
    token_expires_at = (
        now + timedelta(seconds=expires_in_seconds)
        if expires_in_seconds > 0
        else None
    )

    existing = await connected_accounts_collection.find_one({
        "user_id": user_id,
        "platform": platform,
        "platform_user_id": platform_user_id,
    })

    if existing:
        await connected_accounts_collection.update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_expires_at": token_expires_at,
                "status": "active",
                "connected_at": now,
                "account_name": account_name or existing.get("account_name", ""),
                "account_email": account_email or existing.get("account_email", ""),
            }},
        )
        return str(existing["_id"])

    # Count existing accounts for limit enforcement
    count = await connected_accounts_collection.count_documents({
        "user_id": user_id,
        "platform": platform,
    })

    doc = {
        "user_id": user_id,
        "platform": platform,
        "account_type": "personal",
        "account_name": account_name or f"{platform.title()} Account",
        "account_email": account_email,
        "platform_user_id": platform_user_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": token_expires_at,
        "is_default": count == 0,  # first account is default
        "status": "active",
        "connected_at": now,
    }
    result = await connected_accounts_collection.insert_one(doc)
    return str(result.inserted_id)


async def mark_account_expired(account_id: str) -> None:
    """Mark an account's token as expired."""
    await connected_accounts_collection.update_one(
        {"_id": ObjectId(account_id)},
        {"$set": {"status": "expired"}},
    )


async def mark_account_active(account_id: str) -> None:
    """Re-activate an account after successful token refresh."""
    await connected_accounts_collection.update_one(
        {"_id": ObjectId(account_id)},
        {"$set": {"status": "active"}},
    )


async def find_expiring_tokens(
    buffer: timedelta = timedelta(hours=1),
    limit: int = 50,
) -> list[dict]:
    """
    Find accounts whose tokens will expire within `buffer` from now.
    Used by the token refresh worker to proactively refresh before expiry.
    """
    cutoff = datetime.now(timezone.utc) + buffer
    cursor = connected_accounts_collection.find({
        "status": "active",
        "token_expires_at": {"$ne": None, "$lte": cutoff},
        "refresh_token": {"$ne": ""},
    }).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [_serialize_token(doc) for doc in docs]


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _find_account(
    user_id: str,
    platform: str,
    account_id: Optional[str],
) -> Optional[dict]:
    """Find the best matching active account."""
    if account_id:
        return await connected_accounts_collection.find_one({
            "_id": ObjectId(account_id),
            "user_id": user_id,
            "platform": platform,
            "status": "active",
        })

    # Try default first
    doc = await connected_accounts_collection.find_one({
        "user_id": user_id,
        "platform": platform,
        "is_default": True,
        "status": "active",
    })
    if doc:
        return doc

    # Fallback: any active account
    return await connected_accounts_collection.find_one({
        "user_id": user_id,
        "platform": platform,
        "status": "active",
    })


def _is_token_expired(doc: dict) -> bool:
    """Check if a token is expired or about to expire."""
    expires_at = doc.get("token_expires_at")
    if expires_at is None:
        # No expiry recorded — assume valid (e.g. integration tokens)
        return False
    now = datetime.now(timezone.utc)
    # Ensure timezone awareness
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return now + _EXPIRY_BUFFER >= expires_at


def _serialize_token(doc: dict) -> dict:
    """Extract the token fields needed by publishers. Never expose raw doc."""
    return {
        "account_id": str(doc["_id"]),
        "access_token": doc.get("access_token", ""),
        "refresh_token": doc.get("refresh_token", ""),
        "platform_user_id": doc.get("platform_user_id", ""),
        "account_name": doc.get("account_name", ""),
        "platform": doc.get("platform", ""),
        "token_expires_at": doc.get("token_expires_at"),
    }
