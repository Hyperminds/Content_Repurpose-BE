"""External session reference repository (Phase 8).

Stores encrypted references to isolated external browser sessions. Mirrors the
security posture of the credential vault / accounts repo:

  - The raw session reference is ENCRYPTED at rest (Fernet, via CredentialVault).
  - Every query is tenant-scoped.
  - The domain model returned to callers NEVER contains the encrypted reference.
  - Only an internal method (used by the Hermes adapter) can read the decrypted
    reference — it is never surfaced through any API/response.

Trendzzo persists only the reference; the actual browser/session data lives in
an isolated store owned by the Hermes runtime.
"""

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

from app.database import db
from app.social_publishing.credentials.vault import CredentialVault
from app.social_publishing.domain.models import ExternalSessionReference

_collection = db["sp_external_sessions"]


class ExternalSessionRepository:
    """Tenant-scoped storage of encrypted external session references."""

    def __init__(self, vault: Optional[CredentialVault] = None) -> None:
        self._vault = vault or CredentialVault()

    # ── Writes ────────────────────────────────────────────────────────────────

    async def upsert(
        self,
        tenant_id: str,
        provider: str,
        platform: str,
        account_id: str,
        raw_reference: str,
    ) -> ExternalSessionReference:
        """
        Create or replace the session reference for an account.

        `raw_reference` is an opaque handle to isolated session state — NOT
        cookies or session contents. It is encrypted before storage.
        """
        now = datetime.now(timezone.utc)
        encrypted = self._vault.encrypt(raw_reference)

        await _collection.update_one(
            {"tenant_id": tenant_id, "account_id": account_id, "provider": provider},
            {
                "$set": {
                    "tenant_id": tenant_id,
                    "provider": provider,
                    "platform": platform,
                    "account_id": account_id,
                    "encrypted_reference": encrypted,
                    "status": "active",
                    "updated_at": now,
                    "last_verified_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        doc = await _collection.find_one(
            {"tenant_id": tenant_id, "account_id": account_id, "provider": provider}
        )
        return _to_model(doc)

    async def mark_status(
        self, session_id: str, tenant_id: str, status: str
    ) -> bool:
        """Update the session status (active | expired | revoked)."""
        result = await _collection.update_one(
            {"_id": ObjectId(session_id), "tenant_id": tenant_id},
            {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}},
        )
        return result.modified_count > 0

    async def revoke_for_account(self, account_id: str, tenant_id: str) -> int:
        """
        Revoke all session references for an account (e.g. on disconnect).

        Returns the number of references revoked. Does not delete history.
        """
        result = await _collection.update_many(
            {"account_id": account_id, "tenant_id": tenant_id},
            {"$set": {"status": "revoked", "updated_at": datetime.now(timezone.utc)}},
        )
        return result.modified_count

    async def delete_for_account(self, account_id: str, tenant_id: str) -> int:
        """Hard-delete session references for an account."""
        result = await _collection.delete_many(
            {"account_id": account_id, "tenant_id": tenant_id}
        )
        return result.deleted_count

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def find_active(
        self, tenant_id: str, account_id: str, provider: str
    ) -> Optional[ExternalSessionReference]:
        """Return the active session reference model (no encrypted reference)."""
        doc = await _collection.find_one(
            {
                "tenant_id": tenant_id,
                "account_id": account_id,
                "provider": provider,
                "status": "active",
            }
        )
        return _to_model(doc) if doc else None

    async def get_decrypted_reference(
        self, tenant_id: str, account_id: str, provider: str
    ) -> Optional[str]:
        """
        INTERNAL ONLY — decrypt and return the raw session reference.

        Used exclusively by the Hermes execution adapter to attach to the
        isolated session. Must NEVER be exposed through an API response.
        """
        doc = await _collection.find_one(
            {
                "tenant_id": tenant_id,
                "account_id": account_id,
                "provider": provider,
                "status": "active",
            },
            {"encrypted_reference": 1},
        )
        if not doc or not doc.get("encrypted_reference"):
            return None
        return self._vault.decrypt(doc["encrypted_reference"]) or None


# ── Serialization (never includes encrypted_reference) ─────────────────────────

def _to_model(doc: dict) -> ExternalSessionReference:
    return ExternalSessionReference(
        id=str(doc["_id"]),
        tenant_id=doc["tenant_id"],
        provider=doc.get("provider", ""),
        platform=doc.get("platform", ""),
        account_id=doc.get("account_id", ""),
        status=doc.get("status", "active"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
        last_verified_at=doc.get("last_verified_at"),
    )
