"""BYOK credential store (Phase 20).

Stores a customer's own platform API credentials, encrypted at rest and scoped
to a single tenant + account. Mirrors the security posture of the OAuth vault
and the external-session store:

  - values are Fernet-encrypted via CredentialVault
  - every query is tenant + account scoped
  - credentials are NEVER returned to the frontend after storage
  - credentials are NEVER logged
  - only an internal accessor (used by a BYOK provider at publish time) can
    read the decrypted values

This is the storage contract for BYOK. Concrete per-platform BYOK providers use
it; no such provider is implemented yet.
"""

from datetime import datetime, timezone
from typing import Optional

from app.database import db
from app.social_publishing.credentials.vault import CredentialVault

_collection = db["sp_byok_credentials"]


class ByokCredentialStore:
    """Tenant+account-scoped storage of encrypted customer API credentials."""

    def __init__(self, vault: Optional[CredentialVault] = None) -> None:
        self._vault = vault or CredentialVault()

    async def store(
        self,
        tenant_id: str,
        account_id: str,
        provider_name: str,
        credentials: dict,
    ) -> None:
        """
        Encrypt and persist a customer's API credentials for one account.

        `credentials` is a dict of secret values (e.g. {"api_key": "...",
        "api_secret": "..."}). All string values are encrypted before storage.
        Nothing here is logged.
        """
        encrypted = self._vault.encrypt_dict(credentials)
        now = datetime.now(timezone.utc)
        await _collection.update_one(
            {"tenant_id": tenant_id, "account_id": account_id, "provider_name": provider_name},
            {
                "$set": {
                    "tenant_id": tenant_id,
                    "account_id": account_id,
                    "provider_name": provider_name,
                    "encrypted_credentials": encrypted,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def has_credentials(
        self, tenant_id: str, account_id: str, provider_name: str
    ) -> bool:
        """Whether stored credentials exist (no secret values exposed)."""
        doc = await _collection.find_one(
            {"tenant_id": tenant_id, "account_id": account_id, "provider_name": provider_name},
            {"_id": 1},
        )
        return doc is not None

    async def get_decrypted(
        self, tenant_id: str, account_id: str, provider_name: str
    ) -> Optional[dict]:
        """
        INTERNAL ONLY — decrypt and return the customer's credentials.

        Used exclusively by a BYOK provider at publish time. MUST NEVER be
        surfaced through any API response or log line.
        """
        doc = await _collection.find_one(
            {"tenant_id": tenant_id, "account_id": account_id, "provider_name": provider_name},
            {"encrypted_credentials": 1},
        )
        if not doc or not doc.get("encrypted_credentials"):
            return None
        return self._vault.decrypt_dict(doc["encrypted_credentials"])

    async def delete(
        self, tenant_id: str, account_id: str, provider_name: str
    ) -> bool:
        """Remove stored credentials for an account (e.g. on disconnect)."""
        result = await _collection.delete_one(
            {"tenant_id": tenant_id, "account_id": account_id, "provider_name": provider_name}
        )
        return result.deleted_count > 0
