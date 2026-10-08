"""Repository for publishing job documents (audit trail of publish attempts)."""

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

from app.database import db
from app.social_publishing.domain.enums import PostStatus, SocialPlatform
from app.social_publishing.domain.models import PublishingJob

_collection = db["sp_publishing_jobs"]


class PublishingJobsRepository:
    """CRUD operations for publishing jobs."""

    async def create(
        self,
        tenant_id: str,
        post_id: str,
        platform: SocialPlatform,
        attempt_number: int = 1,
    ) -> PublishingJob:
        """Create a new publishing job record."""
        now = datetime.now(timezone.utc)
        doc = {
            "tenant_id": tenant_id,
            "post_id": post_id,
            "platform": platform.value,
            "status": PostStatus.PUBLISHING.value,
            "attempt_number": attempt_number,
            "started_at": now,
            "completed_at": None,
            "error_message": None,
            "platform_post_id": None,
            "created_at": now,
        }
        result = await _collection.insert_one(doc)
        doc["_id"] = result.inserted_id
        return _to_model(doc)

    async def mark_completed(
        self,
        job_id: str,
        platform_post_id: Optional[str] = None,
    ) -> bool:
        """Mark a job as successfully completed."""
        result = await _collection.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {
                "status": PostStatus.PUBLISHED.value,
                "completed_at": datetime.now(timezone.utc),
                "platform_post_id": platform_post_id,
            }},
        )
        return result.modified_count > 0

    async def mark_failed(self, job_id: str, error_message: str) -> bool:
        """Mark a job as failed."""
        result = await _collection.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {
                "status": PostStatus.FAILED.value,
                "completed_at": datetime.now(timezone.utc),
                "error_message": error_message,
            }},
        )
        return result.modified_count > 0

    async def find_by_post(self, post_id: str, tenant_id: str) -> list[PublishingJob]:
        """Get all jobs for a post (tenant-scoped)."""
        cursor = (
            _collection.find({"post_id": post_id, "tenant_id": tenant_id})
            .sort("created_at", -1)
        )
        docs = await cursor.to_list(length=50)
        return [_to_model(d) for d in docs]

    async def find_by_id(self, job_id: str, tenant_id: str) -> Optional[PublishingJob]:
        """Find a specific job by id (tenant-scoped)."""
        doc = await _collection.find_one({
            "_id": ObjectId(job_id),
            "tenant_id": tenant_id,
        })
        return _to_model(doc) if doc else None


# ── Serialization ─────────────────────────────────────────────────────────────

def _to_model(doc: dict) -> PublishingJob:
    """Convert a MongoDB document to a PublishingJob domain model."""
    return PublishingJob(
        id=str(doc["_id"]),
        tenant_id=doc["tenant_id"],
        post_id=doc.get("post_id", ""),
        platform=SocialPlatform(doc["platform"]),
        status=PostStatus(doc["status"]),
        attempt_number=doc.get("attempt_number", 1),
        started_at=doc.get("started_at"),
        completed_at=doc.get("completed_at"),
        error_message=doc.get("error_message"),
        platform_post_id=doc.get("platform_post_id"),
        created_at=doc.get("created_at"),
    )
