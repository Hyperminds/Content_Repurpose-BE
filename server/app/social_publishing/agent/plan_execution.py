"""Plan execution service — converts approved plans into publishing jobs.

This is the bridge between the AI agent layer and the deterministic publishing
engine built in Phase 3. It:
  1. Takes an APPROVED plan
  2. Validates each action is still eligible
  3. Creates SocialPost records in the posts repository
  4. Enqueues publishing jobs in the job queue
  5. Links job IDs back to plan actions
  6. Updates plan status to EXECUTING → COMPLETED/PARTIALLY_FAILED

The execution service NEVER calls platform APIs directly. It only creates
data in the repositories that the Phase 3 worker picks up and publishes.
"""

from datetime import datetime, timezone
from typing import Optional

from app.social_publishing.agent.models import (
    ActionStatus,
    ApprovalMode,
    PlanStatus,
    PublishingPlan,
)
from app.social_publishing.domain.enums import PostStatus, SocialPlatform
from app.social_publishing.domain.exceptions import ValidationError
from app.social_publishing.jobs.repository import JobRepository
from app.social_publishing.repositories.social_posts_repository import SocialPostsRepository
from app.services.logger import log


class PlanExecutionService:
    """Converts approved publishing plans into jobs for the publishing engine."""

    def __init__(
        self,
        posts_repo: SocialPostsRepository,
        job_repo: JobRepository,
    ) -> None:
        self._posts = posts_repo
        self._jobs = job_repo

    async def execute_plan(self, plan: PublishingPlan) -> PublishingPlan:
        """
        Execute an approved plan by creating posts and enqueueing jobs.

        Only APPROVED plans can be executed. Each approved action becomes:
          1. A SocialPost in sp_social_posts (status=SCHEDULED)
          2. A QueuedJob in sp_job_queue (status=PENDING)

        Returns the updated plan with action statuses and job IDs.
        """
        if plan.status != PlanStatus.APPROVED:
            raise ValidationError(
                f"Only approved plans can be executed (current: {plan.status.value})"
            )

        plan.status = PlanStatus.EXECUTING
        plan.updated_at = datetime.now(timezone.utc)

        succeeded = 0
        failed = 0

        for action in plan.actions:
            if action.status != ActionStatus.APPROVED:
                continue

            try:
                job_id = await self._execute_action(plan.tenant_id, action)
                action.status = ActionStatus.QUEUED
                action.job_id = job_id
                succeeded += 1
            except Exception as e:
                action.status = ActionStatus.FAILED
                action.rejection_reason = f"Execution failed: {str(e)[:200]}"
                failed += 1
                log.warning(
                    "Plan action execution failed",
                    plan_id=plan.id,
                    action_id=action.id,
                    error=str(e)[:200],
                )

        # Update plan status
        if failed == 0:
            plan.status = PlanStatus.COMPLETED
        elif succeeded == 0:
            plan.status = PlanStatus.PARTIALLY_FAILED
        else:
            plan.status = PlanStatus.PARTIALLY_FAILED

        plan.updated_at = datetime.now(timezone.utc)

        log.info(
            "Plan execution finished",
            plan_id=plan.id,
            succeeded=succeeded,
            failed=failed,
            total=len(plan.actions),
        )

        return plan

    async def _execute_action(self, tenant_id: str, action) -> str:
        """
        Execute a single plan action:
          1. Create a SocialPost
          2. Enqueue a publishing job
          3. Return the job ID
        """
        # Resolve platform enum
        try:
            platform = SocialPlatform(action.platform)
        except ValueError:
            raise ValueError(f"Unsupported platform: {action.platform}")

        # Create the social post
        post = await self._posts.create(
            tenant_id=tenant_id,
            account_id=action.account_id,
            platform=platform,
            content=action.content_variant.full_content,
            media_urls=action.content_variant.media_urls,
            scheduled_at=action.scheduled_at,
            status=PostStatus.SCHEDULED,
        )

        # Enqueue the job
        idempotency_key = f"agent_plan_{action.id}"
        job = await self._jobs.enqueue(
            tenant_id=tenant_id,
            post_id=post.id,
            account_id=action.account_id,
            platform=platform,
            idempotency_key=idempotency_key,
            scheduled_at=action.scheduled_at,
        )

        if not job:
            # Idempotency collision — job already exists (duplicate execution attempt)
            log.debug("Job already exists for action", action_id=action.id)
            return f"duplicate_{action.id}"

        return job.id
