"""Publishing plan service — orchestrates the AI agent into validated plans.

This is the main entry point for the agent. It:
  1. Takes a user request (e.g., "Create a 7-day campaign")
  2. Generates content variants via ContentGenerationService
  3. Generates a schedule via ScheduleRecommendationService
  4. Assembles a PublishingPlan with ScheduledActions
  5. Validates the plan
  6. Enforces the tenant's approval mode
  7. Persists the plan for review/execution

The plan service NEVER publishes directly. It only produces data structures
that the plan execution service converts into publishing jobs after approval.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from app.social_publishing.agent.content_generation import ContentGenerationService
from app.social_publishing.agent.models import (
    ActionStatus,
    ApprovalMode,
    ContentVariant,
    PlanStatus,
    PublishingPlan,
    ScheduledAction,
)
from app.social_publishing.agent.schedule_recommendation import ScheduleRecommendationService
from app.social_publishing.agent.validation import validate_plan
from app.social_publishing.domain.exceptions import ValidationError
from app.services.logger import log


class PlanService:
    """Orchestrates AI agent decisions into validated publishing plans."""

    def __init__(
        self,
        content_service: Optional[ContentGenerationService] = None,
        schedule_service: Optional[ScheduleRecommendationService] = None,
        plan_repo=None,
    ) -> None:
        self._content = content_service or ContentGenerationService()
        self._schedule = schedule_service or ScheduleRecommendationService()
        self._repo = plan_repo  # PlanRepository or None (lazy init)

    async def _get_repo(self):
        """Lazy-initialize the plan repository."""
        if self._repo is None:
            from app.social_publishing.agent.plan_repository import PlanRepository
            self._repo = PlanRepository()
        return self._repo

    async def _persist(self, plan: PublishingPlan) -> None:
        """Save plan to persistent storage."""
        repo = await self._get_repo()
        await repo.save(plan)

    # ── Plan Generation ───────────────────────────────────────────────────────

    async def generate_campaign_plan(
        self,
        tenant_id: str,
        source_content: str,
        platforms: list[str],
        days: int = 7,
        posts_per_day: int = 1,
        tone: str = "professional",
        context: str = "",
        available_accounts: dict[str, str] = None,
        approval_mode: ApprovalMode = ApprovalMode.MANUAL_APPROVAL,
        timezone_offset_hours: int = 0,
    ) -> PublishingPlan:
        """
        Generate a complete multi-day campaign plan.

        Args:
            tenant_id: Owning tenant.
            source_content: The original idea/content.
            platforms: Platforms to target.
            days: Campaign duration in days.
            posts_per_day: Posts per day across platforms.
            tone: Desired content tone.
            context: Brand/audience context.
            available_accounts: {platform: account_id} mapping.
            approval_mode: Tenant's approval setting.
            timezone_offset_hours: User's timezone.

        Returns:
            A validated PublishingPlan in DRAFT or PENDING_APPROVAL status.
        """
        available_accounts = available_accounts or {}

        # Step 1: Generate content variants for each platform
        variants = await self._content.generate_variants(
            source_content=source_content,
            platforms=platforms,
            tone=tone,
            context=context,
            tenant_id=tenant_id,
        )

        # Build a lookup: platform → variant
        variant_map: dict[str, ContentVariant] = {}
        for v in variants:
            variant_map[v.platform] = v

        # Step 2: Generate schedule
        schedule = self._schedule.generate_campaign_schedule(
            platforms=platforms,
            days=days,
            posts_per_day=posts_per_day,
            timezone_offset_hours=timezone_offset_hours,
        )

        # Step 3: Assemble actions
        actions: list[ScheduledAction] = []
        for slot in schedule:
            platform = slot["platform"]
            scheduled_at = slot["scheduled_at"]
            account_id = available_accounts.get(platform, "")

            # Get content variant (reuse same variant for same platform across days)
            variant = variant_map.get(platform)
            if not variant:
                # Fallback: use source content directly
                variant = ContentVariant(
                    platform=platform,
                    content=source_content[:2000],
                    tone=tone,
                )

            action = ScheduledAction(
                id=_generate_id(),
                platform=platform,
                account_id=account_id,
                content_variant=variant,
                scheduled_at=scheduled_at,
                status=ActionStatus.PENDING,
            )
            actions.append(action)

        # Step 4: Build plan
        plan = PublishingPlan(
            id=_generate_id(),
            tenant_id=tenant_id,
            title=f"{days}-Day Campaign: {source_content[:50]}",
            description=f"AI-generated {days}-day campaign across {', '.join(platforms)}",
            actions=actions,
            status=PlanStatus.DRAFT,
            approval_mode=approval_mode,
            source_content=source_content,
            campaign_days=days,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        # Step 5: Validate
        account_ids = set(available_accounts.values())
        errors = validate_plan(plan, account_ids)
        if errors:
            log.warning("Plan generated with validation issues", errors=errors[:5])
            # Still save as draft — user can fix issues
            plan.description += f" (Has {len(errors)} validation issue(s))"

        # Step 6: Apply approval mode
        if approval_mode == ApprovalMode.AUTO_APPROVE:
            plan.status = PlanStatus.APPROVED
            plan.approved_at = datetime.now(timezone.utc)
            plan.approved_by = "auto_approve"
            for action in plan.actions:
                action.status = ActionStatus.APPROVED
        elif approval_mode == ApprovalMode.AUTO_PUBLISH:
            plan.status = PlanStatus.APPROVED
            plan.approved_at = datetime.now(timezone.utc)
            plan.approved_by = "auto_publish"
            for action in plan.actions:
                action.status = ActionStatus.APPROVED
        else:
            plan.status = PlanStatus.PENDING_APPROVAL

        # Persist
        await self._persist(plan)

        log.info(
            "Publishing plan generated",
            plan_id=plan.id,
            tenant_id=tenant_id,
            actions=len(actions),
            platforms=platforms,
            approval_mode=approval_mode.value,
        )

        return plan

    # ── Single Post Plan ──────────────────────────────────────────────────────

    async def generate_single_post_plan(
        self,
        tenant_id: str,
        source_content: str,
        platform: str,
        account_id: str,
        tone: str = "professional",
        scheduled_at: Optional[datetime] = None,
        approval_mode: ApprovalMode = ApprovalMode.MANUAL_APPROVAL,
        timezone_offset_hours: int = 0,
    ) -> PublishingPlan:
        """Generate a plan for a single post on one platform."""
        # Generate content variant
        variants = await self._content.generate_variants(
            source_content=source_content,
            platforms=[platform],
            tone=tone,
            tenant_id=tenant_id,
        )

        variant = variants[0] if variants else ContentVariant(
            platform=platform, content=source_content[:2000], tone=tone
        )

        # Determine schedule
        if not scheduled_at:
            scheduled_at = self._schedule.recommend_time(
                platform, timezone_offset_hours=timezone_offset_hours
            )

        action = ScheduledAction(
            id=_generate_id(),
            platform=platform,
            account_id=account_id,
            content_variant=variant,
            scheduled_at=scheduled_at,
            status=ActionStatus.PENDING,
        )

        plan = PublishingPlan(
            id=_generate_id(),
            tenant_id=tenant_id,
            title=f"Post: {source_content[:40]}",
            description=f"Single {platform} post",
            actions=[action],
            status=PlanStatus.PENDING_APPROVAL if approval_mode == ApprovalMode.MANUAL_APPROVAL else PlanStatus.APPROVED,
            approval_mode=approval_mode,
            source_content=source_content,
            campaign_days=1,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        if approval_mode != ApprovalMode.MANUAL_APPROVAL:
            plan.approved_at = datetime.now(timezone.utc)
            plan.approved_by = approval_mode.value
            action.status = ActionStatus.APPROVED

        await self._persist(plan)
        return plan

    # ── Plan Management ───────────────────────────────────────────────────────

    async def get_plan(self, plan_id: str, tenant_id: str) -> Optional[PublishingPlan]:
        """Retrieve a plan (tenant-scoped)."""
        repo = await self._get_repo()
        return await repo.find_by_id(plan_id, tenant_id)

    async def list_plans(self, tenant_id: str) -> list[PublishingPlan]:
        """List all plans for a tenant."""
        repo = await self._get_repo()
        return await repo.find_by_tenant(tenant_id)

    async def approve_plan(self, plan_id: str, tenant_id: str, approved_by: str) -> Optional[PublishingPlan]:
        """Approve a pending plan."""
        plan = await self.get_plan(plan_id, tenant_id)
        if not plan:
            return None
        if plan.status != PlanStatus.PENDING_APPROVAL:
            raise ValidationError(f"Plan cannot be approved from status '{plan.status.value}'")

        plan.status = PlanStatus.APPROVED
        plan.approved_at = datetime.now(timezone.utc)
        plan.approved_by = approved_by
        plan.updated_at = datetime.now(timezone.utc)
        for action in plan.actions:
            if action.status == ActionStatus.PENDING:
                action.status = ActionStatus.APPROVED

        await self._persist(plan)
        log.info("Plan approved", plan_id=plan_id, approved_by=approved_by)
        return plan

    async def reject_plan(self, plan_id: str, tenant_id: str, reason: str) -> Optional[PublishingPlan]:
        """Reject a pending plan."""
        plan = await self.get_plan(plan_id, tenant_id)
        if not plan:
            return None
        if plan.status not in (PlanStatus.PENDING_APPROVAL, PlanStatus.DRAFT):
            raise ValidationError(f"Plan cannot be rejected from status '{plan.status.value}'")

        plan.status = PlanStatus.REJECTED
        plan.rejection_reason = reason
        plan.updated_at = datetime.now(timezone.utc)
        for action in plan.actions:
            action.status = ActionStatus.REJECTED
            action.rejection_reason = reason

        await self._persist(plan)
        log.info("Plan rejected", plan_id=plan_id, reason=reason)
        return plan

    async def cancel_plan(self, plan_id: str, tenant_id: str) -> Optional[PublishingPlan]:
        """Cancel a plan (from any non-terminal state)."""
        plan = await self.get_plan(plan_id, tenant_id)
        if not plan:
            return None
        if plan.status in (PlanStatus.COMPLETED, PlanStatus.CANCELLED):
            raise ValidationError(f"Plan cannot be cancelled from status '{plan.status.value}'")

        plan.status = PlanStatus.CANCELLED
        plan.updated_at = datetime.now(timezone.utc)
        await self._persist(plan)
        log.info("Plan cancelled", plan_id=plan_id)
        return plan


def _generate_id() -> str:
    """Generate a short unique ID for plans and actions."""
    return uuid.uuid4().hex[:16]
