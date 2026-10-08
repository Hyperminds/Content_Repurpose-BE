"""API routes for the AI Social Publishing Agent.

Endpoints:
  POST /social-publishing/agent/plans/generate    — Generate a campaign plan
  POST /social-publishing/agent/plans/single      — Generate a single-post plan
  GET  /social-publishing/agent/plans             — List plans for tenant
  GET  /social-publishing/agent/plans/{id}        — Get plan details
  POST /social-publishing/agent/plans/{id}/approve — Approve a plan
  POST /social-publishing/agent/plans/{id}/reject  — Reject a plan
  POST /social-publishing/agent/plans/{id}/execute — Execute an approved plan
  POST /social-publishing/agent/plans/{id}/cancel  — Cancel a plan
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.utils.jwt_handler import get_current_user
from app.core.identity import org_id_from_user, user_id_from_user
from app.social_publishing.agent.models import ApprovalMode, PlanStatus
from app.social_publishing.agent.plan_service import PlanService
from app.social_publishing.agent.plan_execution import PlanExecutionService
from app.social_publishing.domain.exceptions import SocialPublishingError, ValidationError
from app.social_publishing.jobs.repository import JobRepository
from app.social_publishing.repositories.social_posts_repository import SocialPostsRepository

router = APIRouter(prefix="/social-publishing/agent", tags=["social-publishing-agent"])

# ── Service singletons ────────────────────────────────────────────────────────
_plan_service = PlanService()
_execution_service = PlanExecutionService(SocialPostsRepository(), JobRepository())


# ── Request schemas ───────────────────────────────────────────────────────────

class GenerateCampaignRequest(BaseModel):
    source_content: str = Field(..., min_length=1, max_length=5000)
    platforms: list[str] = Field(..., min_length=1)
    days: int = Field(default=7, ge=1, le=90)
    posts_per_day: int = Field(default=1, ge=1, le=5)
    tone: str = Field(default="professional", max_length=50)
    context: str = Field(default="", max_length=2000)
    accounts: dict[str, str] = Field(default_factory=dict, description="platform → account_id")
    approval_mode: str = Field(default="manual_approval")
    timezone_offset_hours: int = Field(default=0, ge=-12, le=14)


class GenerateSinglePostRequest(BaseModel):
    source_content: str = Field(..., min_length=1, max_length=5000)
    platform: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    tone: str = Field(default="professional", max_length=50)
    scheduled_at: Optional[datetime] = None
    approval_mode: str = Field(default="manual_approval")
    timezone_offset_hours: int = Field(default=0, ge=-12, le=14)


class RejectRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


# ── Response helpers ──────────────────────────────────────────────────────────

def _plan_response(plan) -> dict:
    """Convert a PublishingPlan to a JSON-safe response dict."""
    return {
        "id": plan.id,
        "tenant_id": plan.tenant_id,
        "title": plan.title,
        "description": plan.description,
        "status": plan.status.value,
        "approval_mode": plan.approval_mode.value,
        "campaign_days": plan.campaign_days,
        "action_count": plan.action_count,
        "platform_summary": plan.platform_summary,
        "source_content": plan.source_content[:200],
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
        "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
        "approved_by": plan.approved_by,
        "rejection_reason": plan.rejection_reason,
        "actions": [_action_response(a) for a in plan.actions],
    }


def _action_response(action) -> dict:
    return {
        "id": action.id,
        "platform": action.platform,
        "account_id": action.account_id,
        "status": action.status.value,
        "scheduled_at": action.scheduled_at.isoformat() if action.scheduled_at else None,
        "content_preview": action.content_variant.content[:100],
        "hashtags": action.content_variant.hashtags,
        "tone": action.content_variant.tone,
        "job_id": action.job_id,
        "rejection_reason": action.rejection_reason,
    }


def _parse_approval_mode(raw: str) -> ApprovalMode:
    try:
        return ApprovalMode(raw)
    except ValueError:
        return ApprovalMode.MANUAL_APPROVAL


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/plans/generate")
async def generate_campaign_plan(body: GenerateCampaignRequest, user: dict = Depends(get_current_user)):
    """Generate a multi-day campaign publishing plan."""
    tenant_id = org_id_from_user(user)
    approval_mode = _parse_approval_mode(body.approval_mode)

    try:
        plan = await _plan_service.generate_campaign_plan(
            tenant_id=tenant_id,
            source_content=body.source_content,
            platforms=body.platforms,
            days=body.days,
            posts_per_day=body.posts_per_day,
            tone=body.tone,
            context=body.context,
            available_accounts=body.accounts,
            approval_mode=approval_mode,
            timezone_offset_hours=body.timezone_offset_hours,
        )
    except SocialPublishingError as e:
        raise HTTPException(status_code=400, detail=e.message)

    return _plan_response(plan)


@router.post("/plans/single")
async def generate_single_post_plan(body: GenerateSinglePostRequest, user: dict = Depends(get_current_user)):
    """Generate a plan for a single post."""
    tenant_id = org_id_from_user(user)
    approval_mode = _parse_approval_mode(body.approval_mode)

    try:
        plan = await _plan_service.generate_single_post_plan(
            tenant_id=tenant_id,
            source_content=body.source_content,
            platform=body.platform,
            account_id=body.account_id,
            tone=body.tone,
            scheduled_at=body.scheduled_at,
            approval_mode=approval_mode,
            timezone_offset_hours=body.timezone_offset_hours,
        )
    except SocialPublishingError as e:
        raise HTTPException(status_code=400, detail=e.message)

    return _plan_response(plan)


@router.get("/plans")
async def list_plans(user: dict = Depends(get_current_user)):
    """List all plans for the current tenant."""
    tenant_id = org_id_from_user(user)
    plans = await _plan_service.list_plans(tenant_id)
    return {"plans": [_plan_response(p) for p in plans]}


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, user: dict = Depends(get_current_user)):
    """Get a specific plan."""
    tenant_id = org_id_from_user(user)
    plan = await _plan_service.get_plan(plan_id, tenant_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return _plan_response(plan)


@router.post("/plans/{plan_id}/approve")
async def approve_plan(plan_id: str, user: dict = Depends(get_current_user)):
    """Approve a pending plan for execution."""
    tenant_id = org_id_from_user(user)
    user_id = user_id_from_user(user) or tenant_id

    try:
        plan = await _plan_service.approve_plan(plan_id, tenant_id, approved_by=user_id)
    except ValidationError as e:
        raise HTTPException(status_code=409, detail=e.message)

    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return _plan_response(plan)


@router.post("/plans/{plan_id}/reject")
async def reject_plan(plan_id: str, body: RejectRequest, user: dict = Depends(get_current_user)):
    """Reject a pending plan."""
    tenant_id = org_id_from_user(user)

    try:
        plan = await _plan_service.reject_plan(plan_id, tenant_id, reason=body.reason)
    except ValidationError as e:
        raise HTTPException(status_code=409, detail=e.message)

    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return _plan_response(plan)


@router.post("/plans/{plan_id}/execute")
async def execute_plan(plan_id: str, user: dict = Depends(get_current_user)):
    """Execute an approved plan — creates publishing jobs."""
    tenant_id = org_id_from_user(user)
    plan = await _plan_service.get_plan(plan_id, tenant_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    try:
        updated_plan = await _execution_service.execute_plan(plan)
    except ValidationError as e:
        raise HTTPException(status_code=409, detail=e.message)

    return _plan_response(updated_plan)


@router.post("/plans/{plan_id}/cancel")
async def cancel_plan(plan_id: str, user: dict = Depends(get_current_user)):
    """Cancel a plan."""
    tenant_id = org_id_from_user(user)

    try:
        plan = await _plan_service.cancel_plan(plan_id, tenant_id)
    except ValidationError as e:
        raise HTTPException(status_code=409, detail=e.message)

    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return _plan_response(plan)
