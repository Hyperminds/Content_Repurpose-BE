"""Plan repository — persists agent publishing plans to MongoDB.

Replaces the in-memory dict storage in PlanService. Plans survive
application restarts, and tenants can retrieve historical plans.
"""

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

from app.database import db
from app.social_publishing.agent.models import (
    ActionStatus,
    ApprovalMode,
    ContentVariant,
    PlanStatus,
    PublishingPlan,
    ScheduledAction,
)

_collection = db["sp_plans"]


class PlanRepository:
    """MongoDB persistence for publishing plans."""

    async def save(self, plan: PublishingPlan) -> str:
        """Insert or replace a plan. Returns the plan ID."""
        doc = _to_doc(plan)

        # Upsert by plan.id stored in a field (not _id, since plan.id is a hex string)
        existing = await _collection.find_one({"plan_id": plan.id})
        if existing:
            await _collection.replace_one({"plan_id": plan.id}, doc)
            return plan.id

        result = await _collection.insert_one(doc)
        return plan.id

    async def find_by_id(self, plan_id: str, tenant_id: str) -> Optional[PublishingPlan]:
        """Find a plan by ID, scoped to tenant."""
        doc = await _collection.find_one({"plan_id": plan_id, "tenant_id": tenant_id})
        return _to_model(doc) if doc else None

    async def find_by_tenant(
        self, tenant_id: str, limit: int = 50, offset: int = 0
    ) -> list[PublishingPlan]:
        """List plans for a tenant, newest first."""
        cursor = (
            _collection.find({"tenant_id": tenant_id})
            .sort("created_at", -1)
            .skip(offset)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [_to_model(d) for d in docs]

    async def update_status(
        self, plan_id: str, tenant_id: str, updates: dict
    ) -> bool:
        """Update specific fields on a plan."""
        updates["updated_at"] = datetime.now(timezone.utc)
        result = await _collection.update_one(
            {"plan_id": plan_id, "tenant_id": tenant_id},
            {"$set": updates},
        )
        return result.modified_count > 0

    async def delete(self, plan_id: str, tenant_id: str) -> bool:
        """Delete a plan (tenant-scoped)."""
        result = await _collection.delete_one({"plan_id": plan_id, "tenant_id": tenant_id})
        return result.deleted_count > 0


# ── Serialization ─────────────────────────────────────────────────────────────

def _to_doc(plan: PublishingPlan) -> dict:
    """Convert a PublishingPlan to a MongoDB document."""
    return {
        "plan_id": plan.id,
        "tenant_id": plan.tenant_id,
        "title": plan.title,
        "description": plan.description,
        "status": plan.status.value,
        "approval_mode": plan.approval_mode.value,
        "source_content": plan.source_content,
        "campaign_days": plan.campaign_days,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "approved_at": plan.approved_at,
        "approved_by": plan.approved_by,
        "rejection_reason": plan.rejection_reason,
        "actions": [_action_to_doc(a) for a in plan.actions],
    }


def _action_to_doc(action: ScheduledAction) -> dict:
    return {
        "id": action.id,
        "platform": action.platform,
        "account_id": action.account_id,
        "status": action.status.value,
        "scheduled_at": action.scheduled_at,
        "rejection_reason": action.rejection_reason,
        "job_id": action.job_id,
        "published_post_id": action.published_post_id,
        "content_variant": {
            "platform": action.content_variant.platform,
            "content": action.content_variant.content,
            "hashtags": action.content_variant.hashtags,
            "tone": action.content_variant.tone,
            "media_urls": action.content_variant.media_urls,
            "caption_notes": action.content_variant.caption_notes,
        },
    }


def _to_model(doc: dict) -> PublishingPlan:
    """Convert a MongoDB document to a PublishingPlan."""
    actions = [_action_from_doc(a) for a in doc.get("actions", [])]

    return PublishingPlan(
        id=doc["plan_id"],
        tenant_id=doc["tenant_id"],
        title=doc.get("title", ""),
        description=doc.get("description", ""),
        actions=actions,
        status=PlanStatus(doc.get("status", "draft")),
        approval_mode=ApprovalMode(doc.get("approval_mode", "manual_approval")),
        source_content=doc.get("source_content", ""),
        campaign_days=doc.get("campaign_days", 1),
        created_at=doc.get("created_at"),
        updated_at=doc.get("updated_at"),
        approved_at=doc.get("approved_at"),
        approved_by=doc.get("approved_by"),
        rejection_reason=doc.get("rejection_reason"),
    )


def _action_from_doc(doc: dict) -> ScheduledAction:
    cv_doc = doc.get("content_variant", {})
    variant = ContentVariant(
        platform=cv_doc.get("platform", ""),
        content=cv_doc.get("content", ""),
        hashtags=cv_doc.get("hashtags", []),
        tone=cv_doc.get("tone", ""),
        media_urls=cv_doc.get("media_urls", []),
        caption_notes=cv_doc.get("caption_notes", ""),
    )

    return ScheduledAction(
        id=doc.get("id", ""),
        platform=doc.get("platform", ""),
        account_id=doc.get("account_id", ""),
        content_variant=variant,
        scheduled_at=doc.get("scheduled_at"),
        status=ActionStatus(doc.get("status", "pending")),
        rejection_reason=doc.get("rejection_reason"),
        job_id=doc.get("job_id"),
        published_post_id=doc.get("published_post_id"),
    )
