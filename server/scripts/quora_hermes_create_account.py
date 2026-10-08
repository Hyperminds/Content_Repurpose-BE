"""Dev-only: create the minimal Quora user-assisted (Hermes) account record.

This is NOT an OAuth/API connection (Quora has no native API path in Trendzzo).
It represents the Trendzzo-side identity used by the user-assisted Quora Hermes
workflow (browser posting to the user's Quora profile/space). It stores NO Quora
password, token, cookies, session data, or MFA codes.

Field shape mirrors the Instagram/Facebook/Reddit user-assisted accounts:
  platform=quora, provider_type=user_assisted_agent, provider_name=hermes,
  connection_status=connected, is_active=true, has_access_token=false.

Usage (from server/ with the venv python and PYTHONPATH=server):
  venv\\Scripts\\python.exe scripts\\quora_hermes_create_account.py \
      --tenant <tenant_id> --username <quora_username>

Idempotent: reuses an existing quora+username account for the tenant.
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

_SAFE_PROJECTION = {
    "tenant_id": 1, "platform": 1, "provider_type": 1, "provider_name": 1,
    "account_name": 1, "platform_account_id": 1, "connection_status": 1,
    "is_active": 1, "account_type": 1, "has_access_token": 1,
}


async def _find_existing(tenant_id, username):
    return await _collection.find_one(
        {"tenant_id": tenant_id, "platform": SocialPlatform.QUORA.value,
         "platform_account_id": username},
        _SAFE_PROJECTION,
    )


async def create_account(tenant_id, username):
    existing = await _find_existing(tenant_id, username)
    if existing:
        aid = str(existing["_id"])
        print(f"[reuse] Existing Quora Hermes account — id={aid}")
        return aid
    now = datetime.now(timezone.utc)
    doc = {
        "tenant_id": tenant_id,
        "platform": SocialPlatform.QUORA.value,              # "quora"
        "account_name": username,
        "platform_account_id": username,
        "account_type": "personal",
        "is_active": True,
        "connection_status": ConnectionStatus.CONNECTED.value,   # "connected"
        "provider_type": ProviderType.USER_ASSISTED_AGENT.value, # "user_assisted_agent"
        "provider_name": ProviderName.HERMES.value,              # "hermes"
        "has_access_token": False,     # NEVER an API token for Hermes accounts
        "token_expires_at": None,
        "scopes": [],
        "capabilities": [],
        "encrypted_credentials": None,  # no secrets ever
        "created_at": now,
        "updated_at": now,
    }
    result = await _collection.insert_one(doc)
    aid = str(result.inserted_id)
    print(f"[created] Quora Hermes account — id={aid}")
    return aid


async def verify_account(tenant_id, account_id):
    from bson import ObjectId
    doc = await _collection.find_one(
        {"_id": ObjectId(account_id), "tenant_id": tenant_id}, _SAFE_PROJECTION
    )
    if not doc:
        raise SystemExit(f"[FAIL] account {account_id} not found for tenant {tenant_id}")
    checks = {
        "tenant_id": doc.get("tenant_id") == tenant_id,
        "platform == quora": doc.get("platform") == "quora",
        "provider_type == user_assisted_agent": doc.get("provider_type") == "user_assisted_agent",
        "provider_name == hermes": doc.get("provider_name") == "hermes",
        "connection_status == connected": doc.get("connection_status") == "connected",
        "is_active == True": doc.get("is_active") is True,
        "has_access_token == False": doc.get("has_access_token") is False,
    }
    print("\n=== SAFE FIELD VERIFICATION ===")
    for k in ("tenant_id", "platform", "provider_type", "provider_name",
              "account_name", "platform_account_id", "connection_status",
              "is_active", "account_type", "has_access_token"):
        print(f"  {k:22} = {doc.get(k)}")
    print("\n=== ASSERTIONS ===")
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    raw = await _collection.find_one({"_id": ObjectId(account_id)})
    leaked = raw.get("encrypted_credentials") not in (None, {}, [])
    print(f"  [{'FAIL' if leaked else 'PASS'}] no encrypted_credentials stored")
    ok = ok and not leaked
    if not ok:
        raise SystemExit("[FAIL] verification failed")
    print("\n[OK] Account verified with safe fields only.")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--username", required=True, help="Quora username / profile handle")
    args = ap.parse_args()
    aid = await create_account(args.tenant, args.username)
    await verify_account(args.tenant, aid)
    print(f"\nACCOUNT_ID={aid}")


if __name__ == "__main__":
    asyncio.run(main())
