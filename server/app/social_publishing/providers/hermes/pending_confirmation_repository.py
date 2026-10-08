"""Pending-confirmation repository (user-assisted publishing).

Persists a prepared, awaiting-confirmation publish so the SAME operation can be
resumed on confirm / cancelled / expired — keyed by the stable operation_id so
duplicates are impossible.

Every query is tenant-scoped. No cookies/passwords/session contents are stored
here — only the prepared post preview and an opaque browser profile key.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId

from app.database import db
from app.social_publishing.domain.models import PendingConfirmation

_collection = db["sp_pending_confirmations"]


class PendingConfirmationRepository:
    """Tenant-scoped storage for user-assisted pending confirmations."""

    async def create(
        self,
        tenant_id: str,
        account_id: str,
        operation_id: str,
        platform: str,
        provider_name: str,
        *,
        title: str = "",
        body: str = "",
        subreddit: str = "",
        media_paths: Optional[list[str]] = None,
        session_profile_key: str = "",
        ttl_seconds: int = 900,
    ) -> PendingConfirmation:
        """
        Create (or replace) a pending confirmation for an operation_id.

        Upsert on operation_id so a re-prepare of the same operation reuses the
        record (idempotent — never a second pending row for one operation).
        """
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)
        doc = {
            "tenant_id": tenant_id,
            "account_id": account_id,
            "operation_id": operation_id,
            "platform": platform,
            "provider_name": provider_name,
            "status": "action_required",
            "title": title,
            "body": body,
            "subreddit": subreddit,
            "media_paths": list(media_paths or []),
            "session_profile_key": session_profile_key,
            "external_url": None,
            "updated_at": now,
            "expires_at": expires_at,
        }
        await _collection.update_one(
            {"operation_id": operation_id, "tenant_id": tenant_id},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        stored = await _collection.find_one(
            {"operation_id": operation_id, "tenant_id": tenant_id}
        )
        return _to_model(stored)

    async def find(self, confirmation_id: str, tenant_id: str) -> Optional[PendingConfirmation]:
        """Load a pending confirmation by id, tenant-scoped."""
        try:
            oid = ObjectId(confirmation_id)
        except Exception:
            return None
        doc = await _collection.find_one({"_id": oid, "tenant_id": tenant_id})
        return _to_model(doc) if doc else None

    async def find_by_operation(
        self, operation_id: str, tenant_id: str
    ) -> Optional[PendingConfirmation]:
        doc = await _collection.find_one(
            {"operation_id": operation_id, "tenant_id": tenant_id}
        )
        return _to_model(doc) if doc else None

    async def list_awaiting(self, tenant_id: str) -> list[PendingConfirmation]:
        """All confirmations still awaiting the user for a tenant."""
        cursor = _collection.find({
            "tenant_id": tenant_id,
            "status": {"$in": ["action_required", "waiting_for_user"]},
        }).sort("created_at", -1)
        docs = await cursor.to_list(length=100)
        return [_to_model(d) for d in docs]

    async def set_status(
        self,
        confirmation_id: str,
        tenant_id: str,
        status: str,
        *,
        external_url: Optional[str] = None,
    ) -> bool:
        """Transition status (tenant-scoped)."""
        try:
            oid = ObjectId(confirmation_id)
        except Exception:
            return False
        updates: dict = {"status": status, "updated_at": datetime.now(timezone.utc)}
        if external_url is not None:
            updates["external_url"] = external_url
        result = await _collection.update_one(
            {"_id": oid, "tenant_id": tenant_id}, {"$set": updates}
        )
        return result.modified_count > 0

    async def expire_due(self, now: Optional[datetime] = None) -> int:
        """
        Mark all overdue awaiting confirmations as EXPIRED (Phase 12).

        No auto-retry — an expired confirmation simply will not publish. Returns
        the number expired. Runs across tenants (background maintenance).
        """
        now = now or datetime.now(timezone.utc)
        result = await _collection.update_many(
            {
                "status": {"$in": ["action_required", "waiting_for_user"]},
                "expires_at": {"$lte": now},
            },
            {"$set": {"status": "expired", "updated_at": now}},
        )
        return result.modified_count


def _to_model(doc: dict) -> PendingConfirmation:
    return PendingConfirmation(
        id=str(doc["_id"]),
        tenant_id=doc["tenant_id"],
        account_id=doc.get("account_id", ""),
        operation_id=doc.get("operation_id", ""),
        platform=doc.get("platform", ""),
        provider_name=doc.get("provider_name", ""),
        status=doc.get("status", "action_required"),
        title=doc.get("title", ""),
        body=doc.get("body", ""),
        subreddit=doc.get("subreddit", ""),
        media_paths=doc.get("media_paths", []),
        session_profile_key=doc.get("session_profile_key", ""),
        external_url=doc.get("external_url"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
        expires_at=doc.get("expires_at"),
    )
