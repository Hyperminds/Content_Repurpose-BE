"""Dev-only: create the minimal Reddit user-assisted (Hermes) social-account record.

This is NOT an OAuth connection. It represents the Trendzzo-side identity used
by the user-assisted Hermes workflow. It stores NO Reddit password, token,
refresh token, OAuth secret, cookies, session data, or MFA codes.

Field shape mirrors SocialAccountsRepository.create_connected() but with
has_access_token=False and encrypted_credentials=None (user-assisted, no secret).

Usage (from server/ with the venv python):
    venv\\Scripts\\python.exe scripts\\reddit_hermes_create_account.py \
        --tenant 6a3507ed8472b071889873ba --username DinnerOk250

Idempotent: if a reddit account with the same platform_account_id already
exists for the tenant, it reuses it (no duplicate) and re-verifies.
"""

import argparse
import asyncio
from datetime import datetime, timezone

from app.database import db
from app.social_publishing.domain.enums import (
    ConnectionStatus,
    ProviderName,
    ProviderType,
    SocialPlatform,
)

_collection = db["sp_social_accounts"]

# Safe fields only — NEVER project or print credentials/session data.
_SAFE_PROJECTION = {
    "tenant_id": 1,
    "platform": 1,
    "provider_type": 1,
    "provider_name": 1,
    "account_name": 1,
    "platform_account_id": 1,
    "connection_status": 1,
    "is_active": 1,
    "account_type": 1,
    "has_access_token": 1,
    "created_at": 1,
    "updated_at": 1,
}


async def _find_existing(tenant_id: str, platform_account_id: str):
    return await _collection.find_one(
        {
            "tenant_id": tenant_id,
            "platform": SocialPlatform.REDDIT.value,
            "platform_account_id": platform_account_id,
        },
        _SAFE_PROJECTION,
    )


async def create_account(tenant_id: str, username: str) -> str:
    existing = await _find_existing(tenant_id, username)
    if existing:
        account_id = str(existing["_id"])
        print(f"[reuse] Existing Reddit account found for tenant — id={account_id}")
        return account_id

    now = datetime.now(timezone.utc)
    doc = {
        "tenant_id": tenant_id,
        "platform": SocialPlatform.REDDIT.value,               # "reddit"
        "account_name": username,
        "platform_account_id": username,
        "account_type": "personal",
        "is_active": True,
        "connection_status": ConnectionStatus.CONNECTED.value,  # "connected"
        "provider_type": ProviderType.USER_ASSISTED_AGENT.value,  # "user_assisted_agent"
        "provider_name": ProviderName.HERMES.value,             # "hermes"
        # No secrets. Ever.
        "has_access_token": False,
        "token_expires_at": None,
        "scopes": [],
        "capabilities": [],
        "encrypted_credentials": None,
        "created_at": now,
        "updated_at": now,
    }
    result = await _collection.insert_one(doc)
    account_id = str(result.inserted_id)
    print(f"[created] Reddit user-assisted account — id={account_id}")
    return account_id


async def verify_account(tenant_id: str, account_id: str) -> dict:
    from bson import ObjectId

    doc = await _collection.find_one(
        {"_id": ObjectId(account_id), "tenant_id": tenant_id}, _SAFE_PROJECTION
    )
    if not doc:
        raise SystemExit(f"[FAIL] account {account_id} not found for tenant {tenant_id}")

    safe = {
        "account_id": account_id,
        "tenant_id": doc.get("tenant_id"),
        "platform": doc.get("platform"),
        "provider_type": doc.get("provider_type"),
        "provider_name": doc.get("provider_name"),
        "account_name": doc.get("account_name"),
        "platform_account_id": doc.get("platform_account_id"),
        "connection_status": doc.get("connection_status"),
        "is_active": doc.get("is_active"),
        "account_type": doc.get("account_type"),
        "has_access_token": doc.get("has_access_token"),
    }

    print("\n=== SAFE FIELD VERIFICATION ===")
    for k, v in safe.items():
        print(f"  {k:22} = {v}")

    # Assertions
    checks = {
        "tenant_id": safe["tenant_id"] == tenant_id,
        "platform == reddit": safe["platform"] == "reddit",
        "provider_type == user_assisted_agent": safe["provider_type"] == "user_assisted_agent",
        "provider_name == hermes": safe["provider_name"] == "hermes",
        "connection_status == connected": safe["connection_status"] == "connected",
        "is_active == True": safe["is_active"] is True,
        "account_type == personal": safe["account_type"] == "personal",
        "has_access_token == False": safe["has_access_token"] is False,
        "account_id present": bool(safe["account_id"]),
    }
    print("\n=== ASSERTIONS ===")
    all_ok = True
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok

    # Confirm NO secret-bearing fields are populated (defense-in-depth).
    from bson import ObjectId as _OID

    raw = await _collection.find_one({"_id": _OID(account_id)})
    for secret_field in ("encrypted_credentials",):
        val = raw.get(secret_field)
        leaked = val not in (None, {}, [])
        print(f"  [{'FAIL' if leaked else 'PASS'}] no {secret_field} stored")
        all_ok = all_ok and (not leaked)

    if not all_ok:
        raise SystemExit("[FAIL] verification failed")
    print("\n[OK] Account verified with safe fields only.")
    return safe


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()

    account_id = await create_account(args.tenant, args.username)
    await verify_account(args.tenant, account_id)
    print(f"\nACCOUNT_ID={account_id}")


if __name__ == "__main__":
    asyncio.run(main())
