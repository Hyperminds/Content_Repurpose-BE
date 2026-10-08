"""Dev-only: create a Facebook user-assisted (Hermes) account record.

This is NOT an OAuth/Meta-API connection. It represents the Trendzzo-side
identity used by the user-assisted Facebook Hermes workflow (browser posting to
a personal profile OR a Facebook Page). It stores NO Facebook password, token,
refresh token, OAuth secret, cookies, session data, or MFA codes. The native
Meta/Facebook API path is unaffected.

TARGET-AWARE: the workflow decides profile vs page from this account's
account_type ("page" → Page target, otherwise → profile) and uses
platform_account_id as the Page identifier (vanity name, numeric id, or URL)
when it is a Page.

Field shape mirrors the Reddit/Instagram user-assisted accounts:
  platform=facebook, provider_type=user_assisted_agent, provider_name=hermes,
  connection_status=connected, is_active=true, has_access_token=false.

Usage (from server/ with the venv python and PYTHONPATH=server):

  # Personal profile target (identifier is just a label):
  venv\\Scripts\\python.exe scripts\\facebook_hermes_create_account.py \
      --tenant <tenant_id> --target profile --name "My Profile" --identifier me

  # Page target (identifier = the Page's vanity name / id / URL):
  venv\\Scripts\\python.exe scripts\\facebook_hermes_create_account.py \
      --tenant <tenant_id> --target page --name "My Page" --identifier my-page-vanity

Idempotent: reuses an existing facebook account for the tenant with the same
platform_account_id (identifier).
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


async def _find_existing(tenant_id, identifier):
    return await _collection.find_one(
        {"tenant_id": tenant_id, "platform": SocialPlatform.FACEBOOK.value,
         "platform_account_id": identifier},
        _SAFE_PROJECTION,
    )


async def create_account(tenant_id, target, name, identifier):
    # account_type drives the workflow's target resolution: "page" → Page target,
    # anything else → personal profile target.
    account_type = "page" if target == "page" else "personal"
    existing = await _find_existing(tenant_id, identifier)
    if existing:
        aid = str(existing["_id"])
        print(f"[reuse] Existing Facebook Hermes account — id={aid} (target={existing.get('account_type')})")
        return aid
    now = datetime.now(timezone.utc)
    doc = {
        "tenant_id": tenant_id,
        "platform": SocialPlatform.FACEBOOK.value,               # "facebook"
        "account_name": name,
        "platform_account_id": identifier,                       # page vanity/id/url, or profile label
        "account_type": account_type,                            # "page" | "personal"
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
    print(f"[created] Facebook Hermes account — id={aid} (target={account_type})")
    return aid


async def verify_account(tenant_id, account_id, expected_type):
    from bson import ObjectId
    doc = await _collection.find_one(
        {"_id": ObjectId(account_id), "tenant_id": tenant_id}, _SAFE_PROJECTION
    )
    if not doc:
        raise SystemExit(f"[FAIL] account {account_id} not found for tenant {tenant_id}")
    checks = {
        "tenant_id": doc.get("tenant_id") == tenant_id,
        "platform == facebook": doc.get("platform") == "facebook",
        "provider_type == user_assisted_agent": doc.get("provider_type") == "user_assisted_agent",
        "provider_name == hermes": doc.get("provider_name") == "hermes",
        "connection_status == connected": doc.get("connection_status") == "connected",
        "is_active == True": doc.get("is_active") is True,
        "has_access_token == False": doc.get("has_access_token") is False,
        f"account_type == {expected_type}": doc.get("account_type") == expected_type,
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
    ap.add_argument("--target", choices=["profile", "page"], default="profile",
                    help="profile = personal timeline; page = a Facebook Page")
    ap.add_argument("--name", required=True, help="Human-readable account/target name")
    ap.add_argument("--identifier", required=True,
                    help="For page: vanity name / numeric id / URL. For profile: any label (e.g. 'me').")
    args = ap.parse_args()
    expected_type = "page" if args.target == "page" else "personal"
    aid = await create_account(args.tenant, args.target, args.name, args.identifier)
    await verify_account(args.tenant, aid, expected_type)
    print(f"\nACCOUNT_ID={aid}")


if __name__ == "__main__":
    asyncio.run(main())
