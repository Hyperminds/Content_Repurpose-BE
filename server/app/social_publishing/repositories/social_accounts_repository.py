"""Repository for social account documents.

Every query is scoped by tenant_id to enforce multi-tenant isolation at the
data layer. No method exposes cross-tenant data.

Credentials are stored encrypted — the repository handles raw encrypted blobs
without knowing the encryption mechanism (that belongs to CredentialVault).
"""

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

from app.database import db
from app.social_publishing.domain.enums import (
    AccountCapability,
    ConnectionStatus,
    DEFAULT_NATIVE_PROVIDER,
    ProviderType,
    SocialPlatform,
)
from app.social_publishing.domain.models import SocialAccount

_collection = db["sp_social_accounts"]


class SocialAccountsRepository:
    """CRUD operations for social accounts, always tenant-scoped."""

    # ── Queries ───────────────────────────────────────────────────────────────

    async def find_by_id(self, account_id: str, tenant_id: str) -> Optional[SocialAccount]:
        """Find an account by id, scoped to tenant."""
        doc = await _collection.find_one({
            "_id": ObjectId(account_id),
            "tenant_id": tenant_id,
        })
        return _to_model(doc) if doc else None

    async def find_by_platform_account(
        self, tenant_id: str, platform: SocialPlatform, platform_account_id: str
    ) -> Optional[SocialAccount]:
        """Find an existing connection by external platform account id."""
        doc = await _collection.find_one({
            "tenant_id": tenant_id,
            "platform": platform.value,
            "platform_account_id": platform_account_id,
        })
        return _to_model(doc) if doc else None

    async def find_by_tenant(
        self,
        tenant_id: str,
        platform: Optional[SocialPlatform] = None,
        active_only: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SocialAccount]:
        """List accounts for a tenant with optional filters."""
        query: dict = {"tenant_id": tenant_id}
        if platform:
            query["platform"] = platform.value
        if active_only:
            query["is_active"] = True

        cursor = (
            _collection.find(query)
            .sort("created_at", -1)
            .skip(offset)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [_to_model(d) for d in docs]

    async def count_by_tenant(self, tenant_id: str) -> int:
        """Count all accounts for a tenant."""
        return await _collection.count_documents({"tenant_id": tenant_id})

    # ── Mutations ─────────────────────────────────────────────────────────────

    async def create(
        self,
        tenant_id: str,
        platform: SocialPlatform,
        account_name: str,
        platform_account_id: str,
        provider_type: Optional[str] = None,
        provider_name: Optional[str] = None,
    ) -> SocialAccount:
        """
        Create a new social account (minimal — no credentials yet).

        When provider_type/provider_name are supplied (e.g. a user-assisted
        Hermes account added from the UI) they are persisted and the account is
        marked CONNECTED so it is immediately usable; otherwise it defaults to a
        native/pending account (unchanged legacy behavior).
        """
        now = datetime.now(timezone.utc)
        connected = provider_type is not None
        doc = {
            "tenant_id": tenant_id,
            "platform": platform.value,
            "account_name": account_name,
            "platform_account_id": platform_account_id,
            "account_type": "personal",
            "is_active": True,
            "connection_status": (
                ConnectionStatus.CONNECTED.value if connected else ConnectionStatus.PENDING.value
            ),
            "has_access_token": False,
            "token_expires_at": None,
            "scopes": [],
            "capabilities": [],
            "encrypted_credentials": None,
            "created_at": now,
            "updated_at": now,
        }
        if provider_type is not None:
            doc["provider_type"] = provider_type
        if provider_name is not None:
            doc["provider_name"] = provider_name
        result = await _collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return _to_model(doc)

    async def create_connected(
        self,
        tenant_id: str,
        platform: SocialPlatform,
        account_name: str,
        platform_account_id: str,
        account_type: str,
        encrypted_credentials: dict,
        token_expires_at: Optional[datetime],
        scopes: list[str],
        capabilities: list[AccountCapability],
        provider_type: ProviderType = ProviderType.NATIVE_API,
        provider_name: str = "",
    ) -> SocialAccount:
        """Create a fully connected account (after successful OAuth)."""
        now = datetime.now(timezone.utc)
        # Derive a default native provider name from the platform when unset,
        # so existing OAuth flows persist correct provider metadata without
        # any caller changes.
        resolved_provider_name = provider_name or _derive_provider_name(platform)
        doc = {
            "tenant_id": tenant_id,
            "platform": platform.value,
            "account_name": account_name,
            "platform_account_id": platform_account_id,
            "account_type": account_type,
            "is_active": True,
            "connection_status": ConnectionStatus.CONNECTED.value,
            "has_access_token": True,
            "token_expires_at": token_expires_at,
            "scopes": scopes,
            "capabilities": [c.value for c in capabilities],
            "encrypted_credentials": encrypted_credentials,
            "provider_type": provider_type.value,
            "provider_name": resolved_provider_name,
            "created_at": now,
            "updated_at": now,
        }
        result = await _collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return _to_model(doc)

    async def update_credentials(
        self,
        account_id: str,
        tenant_id: str,
        encrypted_credentials: dict,
        token_expires_at: Optional[datetime],
        scopes: Optional[list[str]] = None,
        connection_status: ConnectionStatus = ConnectionStatus.CONNECTED,
    ) -> Optional[SocialAccount]:
        """Update stored credentials (e.g., after token refresh or reconnect)."""
        updates: dict = {
            "encrypted_credentials": encrypted_credentials,
            "token_expires_at": token_expires_at,
            "has_access_token": True,
            "connection_status": connection_status.value,
            "updated_at": datetime.now(timezone.utc),
        }
        if scopes is not None:
            updates["scopes"] = scopes

        result = await _collection.update_one(
            {"_id": ObjectId(account_id), "tenant_id": tenant_id},
            {"$set": updates},
        )
        if result.matched_count == 0:
            return None
        return await self.find_by_id(account_id, tenant_id)

    async def update_connection_status(
        self, account_id: str, tenant_id: str, status: ConnectionStatus
    ) -> bool:
        """Update just the connection status."""
        result = await _collection.update_one(
            {"_id": ObjectId(account_id), "tenant_id": tenant_id},
            {"$set": {
                "connection_status": status.value,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return result.modified_count > 0

    async def update(self, account_id: str, tenant_id: str, updates: dict) -> Optional[SocialAccount]:
        """Update account fields. Only allowed fields are persisted."""
        allowed = {"account_name", "platform_account_id", "is_active",
                   "has_access_token", "token_expires_at", "account_type"}
        safe_updates = {k: v for k, v in updates.items() if k in allowed}
        if not safe_updates:
            return await self.find_by_id(account_id, tenant_id)

        safe_updates["updated_at"] = datetime.now(timezone.utc)
        result = await _collection.update_one(
            {"_id": ObjectId(account_id), "tenant_id": tenant_id},
            {"$set": safe_updates},
        )
        if result.matched_count == 0:
            return None
        return await self.find_by_id(account_id, tenant_id)

    async def deactivate(self, account_id: str, tenant_id: str) -> bool:
        """Soft-delete an account by deactivating it."""
        result = await _collection.update_one(
            {"_id": ObjectId(account_id), "tenant_id": tenant_id},
            {"$set": {
                "is_active": False,
                "connection_status": ConnectionStatus.DISCONNECTED.value,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return result.modified_count > 0

    async def disconnect(self, account_id: str, tenant_id: str) -> bool:
        """Disconnect an account — clears credentials but keeps the record."""
        result = await _collection.update_one(
            {"_id": ObjectId(account_id), "tenant_id": tenant_id},
            {"$set": {
                "encrypted_credentials": None,
                "has_access_token": False,
                "token_expires_at": None,
                "connection_status": ConnectionStatus.DISCONNECTED.value,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return result.modified_count > 0

    async def delete(self, account_id: str, tenant_id: str) -> bool:
        """Hard-delete an account (use with caution)."""
        result = await _collection.delete_one({
            "_id": ObjectId(account_id),
            "tenant_id": tenant_id,
        })
        return result.deleted_count > 0

    async def get_encrypted_credentials(self, account_id: str, tenant_id: str) -> Optional[dict]:
        """
        Retrieve raw encrypted credentials for an account.
        This is the ONLY way to access credentials — used by the publishing orchestrator.
        """
        doc = await _collection.find_one(
            {"_id": ObjectId(account_id), "tenant_id": tenant_id},
            {"encrypted_credentials": 1},
        )
        if not doc:
            return None
        return doc.get("encrypted_credentials")


# ── Serialization ─────────────────────────────────────────────────────────────
# NOTE: Credentials are NEVER included in the model — they stay in the DB layer.

def _to_model(doc: dict) -> SocialAccount:
    """Convert a MongoDB document to a SocialAccount domain model."""
    capabilities_raw = doc.get("capabilities", [])
    capabilities = []
    for c in capabilities_raw:
        try:
            capabilities.append(AccountCapability(c))
        except ValueError:
            pass  # Skip unknown capabilities gracefully

    connection_status = ConnectionStatus.DISCONNECTED
    raw_status = doc.get("connection_status")
    if raw_status:
        try:
            connection_status = ConnectionStatus(raw_status)
        except ValueError:
            pass

    platform = SocialPlatform(doc["platform"])

    # Provider metadata — backfill for accounts created before these fields
    # existed. Missing provider_type → NATIVE_API; missing provider_name →
    # derived from the platform. This keeps all legacy accounts publishing
    # exactly as before.
    provider_type = ProviderType.NATIVE_API
    raw_provider_type = doc.get("provider_type")
    if raw_provider_type:
        try:
            provider_type = ProviderType(raw_provider_type)
        except ValueError:
            pass
    provider_name = doc.get("provider_name") or _derive_provider_name(platform)

    return SocialAccount(
        id=str(doc["_id"]),
        tenant_id=doc["tenant_id"],
        platform=platform,
        account_name=doc.get("account_name", ""),
        platform_account_id=doc.get("platform_account_id", ""),
        is_active=doc.get("is_active", True),
        connection_status=connection_status,
        account_type=doc.get("account_type", "personal"),
        provider_type=provider_type,
        provider_name=provider_name,
        has_access_token=doc.get("has_access_token", False),
        token_expires_at=doc.get("token_expires_at"),
        scopes=doc.get("scopes", []),
        capabilities=capabilities,
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )


def _derive_provider_name(platform: SocialPlatform) -> str:
    """Default provider name for a platform's native integration."""
    provider = DEFAULT_NATIVE_PROVIDER.get(platform)
    return provider.value if provider else platform.value
