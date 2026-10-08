"""Job repository — MongoDB operations for the publishing job queue.

Key design:
  - Atomic claim via findOneAndUpdate (prevents double-execution)
  - Lock-based ownership (worker_id + locked_at)
  - Stuck-job recovery (release jobs locked longer than LOCK_TIMEOUT)
  - Idempotency enforcement (unique idempotency_key)
  - All mutations are single-document atomic operations
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from bson import ObjectId

from app.database import db
from app.social_publishing.domain.enums import SocialPlatform
from app.social_publishing.jobs.models import (
    FailureCategory,
    JobStatus,
    LOCK_TIMEOUT_SECONDS,
    QueuedJob,
)

_collection = db["sp_job_queue"]


def generate_worker_id() -> str:
    """Generate a unique worker identity for lock ownership."""
    return f"worker_{uuid.uuid4().hex[:12]}"


class JobRepository:
    """Atomic, concurrency-safe operations on the publishing job queue."""

    # ── Job creation ──────────────────────────────────────────────────────────

    async def enqueue(
        self,
        tenant_id: str,
        post_id: str,
        account_id: str,
        platform: SocialPlatform,
        idempotency_key: str,
        scheduled_at: Optional[datetime] = None,
        max_attempts: int = 5,
    ) -> Optional[QueuedJob]:
        """
        Create a new job in PENDING state.

        Returns None if a job with the same idempotency_key already exists
        (prevents duplicate jobs for the same post+schedule).
        """
        now = datetime.now(timezone.utc)
        doc = {
            "tenant_id": tenant_id,
            "post_id": post_id,
            "account_id": account_id,
            "platform": platform.value,
            "idempotency_key": idempotency_key,
            "status": JobStatus.PENDING.value,
            "attempts": 0,
            "max_attempts": max_attempts,
            "scheduled_at": scheduled_at,
            "next_retry_at": None,
            "locked_at": None,
            "locked_by": None,
            "started_at": None,
            "completed_at": None,
            "failure_reason": None,
            "failure_category": None,
            "external_post_id": None,
            "created_at": now,
            "updated_at": now,
        }

        try:
            result = await _collection.insert_one(doc)
            doc["_id"] = result.inserted_id
            return _to_model(doc)
        except Exception as e:
            # Duplicate key on idempotency_key → already exists
            if "duplicate key" in str(e).lower() or "E11000" in str(e):
                return None
            raise

    # ── Atomic claim ──────────────────────────────────────────────────────────

    async def claim_next(self, worker_id: str) -> Optional[QueuedJob]:
        """
        Atomically claim the next available job.

        Uses findOneAndUpdate to guarantee only one worker gets a given job,
        even under concurrency. Returns None if no jobs are available.

        Eligible jobs: PENDING status, or RETRYING with next_retry_at <= now.
        """
        now = datetime.now(timezone.utc)

        doc = await _collection.find_one_and_update(
            {
                "$or": [
                    {"status": JobStatus.PENDING.value},
                    {
                        "status": JobStatus.RETRYING.value,
                        "next_retry_at": {"$lte": now},
                    },
                ],
            },
            {
                "$set": {
                    "status": JobStatus.CLAIMED.value,
                    "locked_at": now,
                    "locked_by": worker_id,
                    "updated_at": now,
                },
            },
            sort=[("created_at", 1)],  # FIFO ordering
            return_document=True,
        )

        return _to_model(doc) if doc else None

    # ── Execution tracking ────────────────────────────────────────────────────

    async def mark_executing(self, job_id: str, worker_id: str) -> bool:
        """Transition CLAIMED → EXECUTING. Verifies lock ownership."""
        now = datetime.now(timezone.utc)
        result = await _collection.update_one(
            {
                "_id": ObjectId(job_id),
                "status": JobStatus.CLAIMED.value,
                "locked_by": worker_id,
            },
            [
                {"$set": {
                    "status": JobStatus.EXECUTING.value,
                    "started_at": now,
                    "attempts": {"$add": ["$attempts", 1]},
                    "updated_at": now,
                }},
            ],
        )
        return result.modified_count > 0

    async def mark_completed(
        self,
        job_id: str,
        worker_id: str,
        external_post_id: Optional[str] = None,
    ) -> bool:
        """Mark a job as successfully completed."""
        now = datetime.now(timezone.utc)
        result = await _collection.update_one(
            {
                "_id": ObjectId(job_id),
                "locked_by": worker_id,
                "status": {"$in": [JobStatus.EXECUTING.value, JobStatus.CLAIMED.value]},
            },
            {"$set": {
                "status": JobStatus.COMPLETED.value,
                "completed_at": now,
                "external_post_id": external_post_id,
                "locked_at": None,
                "locked_by": None,
                "updated_at": now,
            }},
        )
        return result.modified_count > 0

    async def mark_failed(
        self,
        job_id: str,
        worker_id: str,
        reason: str,
        category: FailureCategory,
        next_retry_at: Optional[datetime] = None,
    ) -> bool:
        """
        Mark a job as failed. If next_retry_at is provided, status becomes
        RETRYING (eligible for future pickup). Otherwise, permanently FAILED.
        """
        now = datetime.now(timezone.utc)
        new_status = JobStatus.RETRYING if next_retry_at else JobStatus.FAILED

        result = await _collection.update_one(
            {
                "_id": ObjectId(job_id),
                "locked_by": worker_id,
                "status": {"$in": [
                    JobStatus.EXECUTING.value,
                    JobStatus.CLAIMED.value,
                ]},
            },
            {"$set": {
                "status": new_status.value,
                "failure_reason": reason,
                "failure_category": category.value,
                "next_retry_at": next_retry_at,
                "completed_at": now if new_status == JobStatus.FAILED else None,
                "locked_at": None,
                "locked_by": None,
                "updated_at": now,
            }},
        )
        return result.modified_count > 0

    async def cancel(self, job_id: str, tenant_id: str) -> bool:
        """Cancel a pending/retrying job (tenant-scoped)."""
        result = await _collection.update_one(
            {
                "_id": ObjectId(job_id),
                "tenant_id": tenant_id,
                "status": {"$in": [
                    JobStatus.PENDING.value,
                    JobStatus.RETRYING.value,
                ]},
            },
            {"$set": {
                "status": JobStatus.CANCELLED.value,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return result.modified_count > 0

    # ── Stuck job recovery ────────────────────────────────────────────────────

    async def release_stuck_jobs(self, timeout_seconds: int = LOCK_TIMEOUT_SECONDS) -> int:
        """
        Find jobs that have been CLAIMED/EXECUTING longer than the timeout
        and release them back to RETRYING (or FAILED if max attempts reached).

        This handles worker crashes — if a worker dies mid-execution, the lock
        eventually expires and the job becomes available again.

        Returns the number of jobs released.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)

        # Find stuck jobs
        cursor = _collection.find({
            "status": {"$in": [JobStatus.CLAIMED.value, JobStatus.EXECUTING.value]},
            "locked_at": {"$lte": cutoff},
        })
        stuck = await cursor.to_list(length=100)

        released = 0
        now = datetime.now(timezone.utc)
        for doc in stuck:
            attempts = doc.get("attempts", 0)
            max_attempts = doc.get("max_attempts", 5)

            if attempts >= max_attempts:
                new_status = JobStatus.FAILED.value
                next_retry = None
            else:
                new_status = JobStatus.RETRYING.value
                # Retry in 60 seconds after crash recovery
                next_retry = now + timedelta(seconds=60)

            await _collection.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "status": new_status,
                    "locked_at": None,
                    "locked_by": None,
                    "failure_reason": "Worker timeout — job released for retry",
                    "failure_category": FailureCategory.TEMPORARY.value,
                    "next_retry_at": next_retry,
                    "updated_at": now,
                }},
            )
            released += 1

        return released

    # ── Queries ───────────────────────────────────────────────────────────────

    async def find_by_id(self, job_id: str) -> Optional[QueuedJob]:
        """Find a job by id."""
        doc = await _collection.find_one({"_id": ObjectId(job_id)})
        return _to_model(doc) if doc else None

    async def find_by_post(self, post_id: str, tenant_id: str) -> list[QueuedJob]:
        """Find all jobs for a post (tenant-scoped)."""
        cursor = _collection.find({
            "post_id": post_id,
            "tenant_id": tenant_id,
        }).sort("created_at", -1)
        docs = await cursor.to_list(length=50)
        return [_to_model(d) for d in docs]

    async def find_by_idempotency_key(self, key: str) -> Optional[QueuedJob]:
        """Find a job by its idempotency key."""
        doc = await _collection.find_one({"idempotency_key": key})
        return _to_model(doc) if doc else None

    async def count_by_status(self) -> dict[str, int]:
        """Get job counts grouped by status (for metrics)."""
        pipeline = [
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
        results = await _collection.aggregate(pipeline).to_list(length=20)
        return {r["_id"]: r["count"] for r in results}

    async def pending_count(self) -> int:
        """Count jobs waiting to be processed."""
        return await _collection.count_documents({
            "status": {"$in": [JobStatus.PENDING.value, JobStatus.RETRYING.value]},
        })


# ── Serialization ─────────────────────────────────────────────────────────────

def _to_model(doc: dict) -> QueuedJob:
    fc_raw = doc.get("failure_category")
    failure_category = None
    if fc_raw:
        try:
            failure_category = FailureCategory(fc_raw)
        except ValueError:
            pass

    return QueuedJob(
        id=str(doc["_id"]),
        tenant_id=doc["tenant_id"],
        post_id=doc["post_id"],
        account_id=doc.get("account_id", ""),
        platform=SocialPlatform(doc["platform"]),
        idempotency_key=doc.get("idempotency_key", ""),
        status=JobStatus(doc["status"]),
        attempts=doc.get("attempts", 0),
        max_attempts=doc.get("max_attempts", 5),
        scheduled_at=doc.get("scheduled_at"),
        next_retry_at=doc.get("next_retry_at"),
        locked_at=doc.get("locked_at"),
        locked_by=doc.get("locked_by"),
        started_at=doc.get("started_at"),
        completed_at=doc.get("completed_at"),
        failure_reason=doc.get("failure_reason"),
        failure_category=failure_category,
        external_post_id=doc.get("external_post_id"),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
    )
