"""Repository for social post documents.

All queries enforce tenant_id scoping. The scheduler also uses a dedicated
method to find due posts across all tenants (for background processing).
"""

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

from app.database import db
from app.social_publishing.domain.enums import PostStatus, SocialPlatform
from app.social_publishing.domain.models import SocialPost

_collection = db["sp_social_posts"]


class SocialPostsRepository:
    """CRUD and query operations for social posts."""

    # ── Queries ───────────────────────────────────────────────────────────────

    async def find_by_id(self, post_id: str, tenant_id: str) -> Optional[SocialPost]:
        """Find a post by id, scoped to tenant."""
        doc = await _collection.find_one({
            "_id": ObjectId(post_id),
            "tenant_id": tenant_id,
        })
        return _to_model(doc) if doc else None

    async def find_by_id_any_tenant(self, post_id: str) -> Optional[SocialPost]:
        """Find a post by id regardless of tenant (for scheduler/worker use only)."""
        doc = await _collection.find_one({"_id": ObjectId(post_id)})
        return _to_model(doc) if doc else None

    async def find_by_tenant(
        self,
        tenant_id: str,
        status: Optional[PostStatus] = None,
        platform: Optional[SocialPlatform] = None,
        account_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SocialPost]:
        """List posts for a tenant with optional filters."""
        query: dict = {"tenant_id": tenant_id}
        if status:
            query["status"] = status.value
        if platform:
            query["platform"] = platform.value
        if account_id:
            query["account_id"] = account_id

        cursor = (
            _collection.find(query)
            .sort("created_at", -1)
            .skip(offset)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [_to_model(d) for d in docs]

    async def count_by_tenant(
        self, tenant_id: str, status: Optional[PostStatus] = None
    ) -> int:
        """Count posts for a tenant."""
        query: dict = {"tenant_id": tenant_id}
        if status:
            query["status"] = status.value
        return await _collection.count_documents(query)

    async def find_due_for_scheduling(self, now: datetime, limit: int = 50) -> list[SocialPost]:
        """
        Find posts that are SCHEDULED and whose scheduled_at has passed.
        Used by the background scheduler — NOT tenant-scoped (processes all tenants).
        """
        cursor = _collection.find({
            "status": PostStatus.SCHEDULED.value,
            "scheduled_at": {"$lte": now},
        }).limit(limit)
        docs = await cursor.to_list(length=limit)
        return [_to_model(d) for d in docs]

    async def find_retryable(self, now: datetime, limit: int = 20) -> list[SocialPost]:
        """Find FAILED posts eligible for retry."""
        cursor = _collection.find({
            "status": PostStatus.FAILED.value,
            "retry_count": {"$lt": 5},  # will be compared against max_retries per-doc
        }).limit(limit)

        results: list[SocialPost] = []
        for doc in await cursor.to_list(length=limit):
            post = _to_model(doc)
            if post.retry_count < post.max_retries:
                results.append(post)
        return results

    # ── Mutations ─────────────────────────────────────────────────────────────

    async def create(
        self,
        tenant_id: str,
        account_id: str,
        platform: SocialPlatform,
        content: str,
        media_urls: Optional[list[str]] = None,
        scheduled_at: Optional[datetime] = None,
        status: PostStatus = PostStatus.DRAFT,
    ) -> SocialPost:
        """Create a new social post."""
        now = datetime.now(timezone.utc)
        doc = {
            "tenant_id": tenant_id,
            "account_id": account_id,
            "platform": platform.value,
            "status": status.value,
            "content": content,
            "media_urls": media_urls or [],
            "scheduled_at": scheduled_at,
            "published_at": None,
            "failure_reason": None,
            "retry_count": 0,
            "max_retries": 5,
            "platform_post_id": None,
            "created_at": now,
            "updated_at": now,
        }
        result = await _collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return _to_model(doc)

    async def update_status(
        self,
        post_id: str,
        tenant_id: str,
        new_status: PostStatus,
        extra_fields: Optional[dict] = None,
    ) -> Optional[SocialPost]:
        """Update a post's status with optional additional field updates."""
        updates: dict = {
            "status": new_status.value,
            "updated_at": datetime.now(timezone.utc),
        }
        if extra_fields:
            updates.update(extra_fields)

        result = await _collection.update_one(
            {"_id": ObjectId(post_id), "tenant_id": tenant_id},
            {"$set": updates},
        )
        if result.matched_count == 0:
            return None
        return await self.find_by_id(post_id, tenant_id)

    async def update_status_by_id(
        self,
        post_id: str,
        new_status: PostStatus,
        extra_fields: Optional[dict] = None,
    ) -> bool:
        """
        Update post status without tenant check (for scheduler/worker).
        Returns True if the document was updated.
        """
        updates: dict = {
            "status": new_status.value,
            "updated_at": datetime.now(timezone.utc),
        }
        if extra_fields:
            updates.update(extra_fields)

        result = await _collection.update_one(
            {"_id": ObjectId(post_id)},
            {"$set": updates},
        )
        return result.modified_count > 0

    async def update_fields(self, post_id: str, tenant_id: str, updates: dict) -> Optional[SocialPost]:
        """Update allowed fields on a post."""
        allowed = {"content", "media_urls", "scheduled_at"}
        safe_updates = {k: v for k, v in updates.items() if k in allowed}
        if not safe_updates:
            return await self.find_by_id(post_id, tenant_id)

        safe_updates["updated_at"] = datetime.now(timezone.utc)
        result = await _collection.update_one(
            {"_id": ObjectId(post_id), "tenant_id": tenant_id},
            {"$set": safe_updates},
        )
        if result.matched_count == 0:
            return None
        return await self.find_by_id(post_id, tenant_id)

    async def delete(self, post_id: str, tenant_id: str) -> bool:
        """Hard-delete a post (only allowed in DRAFT or CANCELLED states)."""
        result = await _collection.delete_one({
            "_id": ObjectId(post_id),
            "tenant_id": tenant_id,
            "status": {"$in": [PostStatus.DRAFT.value, PostStatus.CANCELLED.value]},
        })
        return result.deleted_count > 0


# ── Serialization ─────────────────────────────────────────────────────────────

def _to_model(doc: dict) -> SocialPost:
    """Convert a MongoDB document to a SocialPost domain model."""
    return SocialPost(
        id=str(doc["_id"]),
        tenant_id=doc["tenant_id"],
        account_id=doc.get("account_id", ""),
        platform=SocialPlatform(doc["platform"]),
        status=PostStatus(doc["status"]),
        content=doc.get("content", ""),
        media_urls=doc.get("media_urls", []),
        scheduled_at=doc.get("scheduled_at"),
        published_at=doc.get("published_at"),
        failure_reason=doc.get("failure_reason"),
        retry_count=doc.get("retry_count", 0),
        max_retries=doc.get("max_retries", 5),
        platform_post_id=doc.get("platform_post_id"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )
