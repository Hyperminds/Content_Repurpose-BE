"""Tests for the AI Social Publishing Agent.

Covers:
  - Plan validation (invalid AI output, unsupported platforms, unavailable accounts)
  - Approval enforcement (manual, auto_approve, auto_publish)
  - Tenant isolation (cannot access other tenant's plans)
  - Content generation (handles malformed LLM output gracefully)
  - Schedule recommendation (future dates, platform coverage)
  - Plan execution (creates jobs, rejects unapproved plans)
  - Invalid schedule (past dates, too far ahead)
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.social_publishing.agent.models import (
    ActionStatus,
    ApprovalMode,
    ContentVariant,
    PlanStatus,
    PublishingPlan,
    ScheduledAction,
)
from app.social_publishing.agent.validation import (
    validate_plan,
    validate_content_variant,
    validate_schedule_time,
)
from app.social_publishing.agent.content_generation import ContentGenerationService
from app.social_publishing.agent.schedule_recommendation import ScheduleRecommendationService
from app.social_publishing.agent.plan_service import PlanService
from app.social_publishing.agent.plan_execution import PlanExecutionService
from app.social_publishing.domain.exceptions import ValidationError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_plan(
    tenant_id: str = "tenant_1",
    status: PlanStatus = PlanStatus.PENDING_APPROVAL,
    actions: list = None,
    approval_mode: ApprovalMode = ApprovalMode.MANUAL_APPROVAL,
) -> PublishingPlan:
    if actions is None:
        actions = [_make_action()]
    return PublishingPlan(
        id="plan_1",
        tenant_id=tenant_id,
        title="Test Plan",
        description="Test",
        actions=actions,
        status=status,
        approval_mode=approval_mode,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _make_action(
    platform: str = "linkedin",
    account_id: str = "acc_1",
    content: str = "Hello world",
    scheduled_at: datetime = None,
) -> ScheduledAction:
    if scheduled_at is None:
        scheduled_at = datetime.now(timezone.utc) + timedelta(hours=2)
    return ScheduledAction(
        id="action_1",
        platform=platform,
        account_id=account_id,
        content_variant=ContentVariant(platform=platform, content=content),
        scheduled_at=scheduled_at,
    )


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION — INVALID AI OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

class TestPlanValidation:
    def test_valid_plan_passes(self):
        plan = _make_plan()
        errors = validate_plan(plan, available_accounts={"acc_1"})
        assert errors == []

    def test_empty_title_fails(self):
        plan = _make_plan()
        plan.title = ""
        errors = validate_plan(plan, available_accounts={"acc_1"})
        assert any("title" in e.lower() for e in errors)

    def test_no_actions_fails(self):
        plan = _make_plan(actions=[])
        errors = validate_plan(plan, available_accounts={"acc_1"})
        assert any("at least one action" in e.lower() for e in errors)

    def test_unsupported_platform_fails(self):
        action = _make_action(platform="tiktok")
        plan = _make_plan(actions=[action])
        errors = validate_plan(plan, available_accounts={"acc_1"})
        assert any("unsupported platform" in e.lower() for e in errors)

    def test_unavailable_account_fails(self):
        action = _make_action(account_id="acc_not_owned")
        plan = _make_plan(actions=[action])
        errors = validate_plan(plan, available_accounts={"acc_1"})
        assert any("not available" in e.lower() for e in errors)

    def test_empty_content_fails(self):
        action = _make_action(content="")
        plan = _make_plan(actions=[action])
        errors = validate_plan(plan, available_accounts={"acc_1"})
        assert any("empty" in e.lower() for e in errors)

    def test_content_too_long_for_platform(self):
        long_content = "x" * 3001  # LinkedIn limit is 3000
        action = _make_action(platform="linkedin", content=long_content)
        plan = _make_plan(actions=[action])
        errors = validate_plan(plan, available_accounts={"acc_1"})
        assert any("exceeds" in e.lower() for e in errors)

    def test_schedule_in_past_fails(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        action = _make_action(scheduled_at=past)
        plan = _make_plan(actions=[action])
        errors = validate_plan(plan, available_accounts={"acc_1"})
        assert any("future" in e.lower() for e in errors)

    def test_schedule_too_far_ahead_fails(self):
        far_future = datetime.now(timezone.utc) + timedelta(days=100)
        action = _make_action(scheduled_at=far_future)
        plan = _make_plan(actions=[action])
        errors = validate_plan(plan, available_accounts={"acc_1"})
        assert any("90 days" in e.lower() for e in errors)

    def test_hashtag_with_spaces_fails(self):
        variant = ContentVariant(platform="linkedin", content="Hello", hashtags=["two words"])
        errors = validate_content_variant(variant, "linkedin", "Test")
        assert any("spaces" in e.lower() for e in errors)


# ══════════════════════════════════════════════════════════════════════════════
# APPROVAL ENFORCEMENT
# ══════════════════════════════════════════════════════════════════════════════

class TestApprovalEnforcement:
    @pytest.mark.asyncio
    async def test_manual_approval_starts_pending(self):
        svc = _make_plan_service()
        plan = await svc.generate_single_post_plan(
            tenant_id="t1", source_content="Hello",
            platform="linkedin", account_id="acc_1",
            approval_mode=ApprovalMode.MANUAL_APPROVAL,
        )
        assert plan.status == PlanStatus.PENDING_APPROVAL
        assert plan.actions[0].status == ActionStatus.PENDING

    @pytest.mark.asyncio
    async def test_auto_approve_starts_approved(self):
        svc = _make_plan_service()
        plan = await svc.generate_single_post_plan(
            tenant_id="t1", source_content="Hello",
            platform="linkedin", account_id="acc_1",
            approval_mode=ApprovalMode.AUTO_APPROVE,
        )
        assert plan.status == PlanStatus.APPROVED
        assert plan.actions[0].status == ActionStatus.APPROVED
        assert plan.approved_by == "auto_approve"

    @pytest.mark.asyncio
    async def test_auto_publish_starts_approved(self):
        svc = _make_plan_service()
        plan = await svc.generate_single_post_plan(
            tenant_id="t1", source_content="Hello",
            platform="linkedin", account_id="acc_1",
            approval_mode=ApprovalMode.AUTO_PUBLISH,
        )
        assert plan.status == PlanStatus.APPROVED
        assert plan.approved_by == "auto_publish"

    @pytest.mark.asyncio
    async def test_approve_transitions_plan(self):
        svc = _make_plan_service()
        plan = await svc.generate_single_post_plan(
            tenant_id="t1", source_content="Test",
            platform="linkedin", account_id="acc_1",
            approval_mode=ApprovalMode.MANUAL_APPROVAL,
        )
        approved = await svc.approve_plan(plan.id, "t1", "user_123")
        assert approved.status == PlanStatus.APPROVED
        assert approved.approved_by == "user_123"
        assert approved.actions[0].status == ActionStatus.APPROVED

    @pytest.mark.asyncio
    async def test_reject_transitions_plan(self):
        svc = _make_plan_service()
        plan = await svc.generate_single_post_plan(
            tenant_id="t1", source_content="Test",
            platform="linkedin", account_id="acc_1",
        )
        rejected = await svc.reject_plan(plan.id, "t1", "Not suitable")
        assert rejected.status == PlanStatus.REJECTED
        assert rejected.rejection_reason == "Not suitable"

    @pytest.mark.asyncio
    async def test_cannot_approve_already_rejected(self):
        svc = _make_plan_service()
        plan = await svc.generate_single_post_plan(
            tenant_id="t1", source_content="Test",
            platform="linkedin", account_id="acc_1",
        )
        await svc.reject_plan(plan.id, "t1", "No")
        with pytest.raises(ValidationError):
            await svc.approve_plan(plan.id, "t1", "user")

    @pytest.mark.asyncio
    async def test_cannot_execute_unapproved_plan(self):
        posts_repo = AsyncMock()
        job_repo = AsyncMock()
        exec_svc = PlanExecutionService(posts_repo, job_repo)
        plan = _make_plan(status=PlanStatus.PENDING_APPROVAL)

        with pytest.raises(ValidationError):
            await exec_svc.execute_plan(plan)


# ══════════════════════════════════════════════════════════════════════════════
# TENANT ISOLATION
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentTenantIsolation:
    @pytest.mark.asyncio
    async def test_cannot_get_other_tenants_plan(self):
        svc = _make_plan_service()
        plan = await svc.generate_single_post_plan(
            tenant_id="tenant_A", source_content="Hello",
            platform="linkedin", account_id="acc_1",
        )
        # Tenant B cannot access Tenant A's plan
        result = await svc.get_plan(plan.id, "tenant_B")
        assert result is None

    @pytest.mark.asyncio
    async def test_cannot_approve_other_tenants_plan(self):
        svc = _make_plan_service()
        plan = await svc.generate_single_post_plan(
            tenant_id="tenant_A", source_content="Hello",
            platform="linkedin", account_id="acc_1",
        )
        result = await svc.approve_plan(plan.id, "tenant_B", "attacker")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_only_own_plans(self):
        svc = _make_plan_service()
        await svc.generate_single_post_plan(
            tenant_id="tenant_A", source_content="A's plan",
            platform="linkedin", account_id="acc_1",
        )
        await svc.generate_single_post_plan(
            tenant_id="tenant_B", source_content="B's plan",
            platform="linkedin", account_id="acc_2",
        )
        a_plans = await svc.list_plans("tenant_A")
        b_plans = await svc.list_plans("tenant_B")
        assert len(a_plans) == 1
        assert len(b_plans) == 1
        assert a_plans[0].tenant_id == "tenant_A"


# ══════════════════════════════════════════════════════════════════════════════
# CONTENT GENERATION — MALFORMED OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

class TestContentGenerationRobustness:
    @pytest.mark.asyncio
    async def test_handles_empty_ai_response(self):
        svc = ContentGenerationService(ai_client=_mock_ai_client(""))
        variants = await svc.generate_variants("Hello", ["linkedin"])
        # Should not crash; may return empty or fallback
        assert isinstance(variants, list)

    @pytest.mark.asyncio
    async def test_handles_invalid_json_gracefully(self):
        svc = ContentGenerationService(ai_client=_mock_ai_client("This is not JSON at all"))
        variants = await svc.generate_variants("Hello", ["linkedin"])
        # Should still produce a variant using raw text as content
        assert len(variants) == 1
        assert "This is not JSON" in variants[0].content

    @pytest.mark.asyncio
    async def test_handles_json_with_markdown_fences(self):
        response = '```json\n{"content": "Test post", "hashtags": ["ai", "marketing"]}\n```'
        svc = ContentGenerationService(ai_client=_mock_ai_client(response))
        variants = await svc.generate_variants("Hello", ["linkedin"])
        assert len(variants) == 1
        assert variants[0].content == "Test post"
        assert "ai" in variants[0].hashtags

    @pytest.mark.asyncio
    async def test_handles_ai_exception(self):
        client = AsyncMock()
        client.chat.completions.create.side_effect = Exception("API timeout")
        svc = ContentGenerationService(ai_client=client)
        variants = await svc.generate_variants("Hello", ["linkedin"])
        # Should not crash
        assert isinstance(variants, list)


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULE RECOMMENDATION
# ══════════════════════════════════════════════════════════════════════════════

class TestScheduleRecommendation:
    def test_recommended_time_is_in_future(self):
        svc = ScheduleRecommendationService()
        now = datetime.now(timezone.utc)
        recommended = svc.recommend_time("linkedin")
        assert recommended > now

    def test_campaign_schedule_has_correct_days(self):
        svc = ScheduleRecommendationService()
        schedule = svc.generate_campaign_schedule(
            platforms=["linkedin", "instagram"], days=5, posts_per_day=1
        )
        assert len(schedule) == 5

    def test_campaign_schedule_distributes_platforms(self):
        svc = ScheduleRecommendationService()
        schedule = svc.generate_campaign_schedule(
            platforms=["linkedin", "instagram", "meta"], days=6, posts_per_day=1
        )
        platforms_used = {s["platform"] for s in schedule}
        assert "linkedin" in platforms_used
        assert "instagram" in platforms_used
        assert "meta" in platforms_used

    def test_campaign_multiple_posts_per_day(self):
        svc = ScheduleRecommendationService()
        schedule = svc.generate_campaign_schedule(
            platforms=["linkedin"], days=3, posts_per_day=2
        )
        assert len(schedule) == 6


# ══════════════════════════════════════════════════════════════════════════════
# PLAN EXECUTION
# ══════════════════════════════════════════════════════════════════════════════

class TestPlanExecution:
    @pytest.mark.asyncio
    async def test_execute_approved_plan_creates_jobs(self):
        posts_repo = AsyncMock()
        job_repo = AsyncMock()

        from app.social_publishing.domain.models import SocialPost
        from app.social_publishing.domain.enums import PostStatus as PS, SocialPlatform as SP
        mock_post = SocialPost(id="post_new", tenant_id="t1", account_id="acc_1",
                               platform=SP.LINKEDIN, status=PS.SCHEDULED, content="x")
        posts_repo.create.return_value = mock_post

        from app.social_publishing.jobs.models import QueuedJob, JobStatus
        mock_job = QueuedJob(id="job_new", tenant_id="t1", post_id="post_new",
                             account_id="acc_1", platform=SP.LINKEDIN,
                             idempotency_key="k", status=JobStatus.PENDING)
        job_repo.enqueue.return_value = mock_job

        exec_svc = PlanExecutionService(posts_repo, job_repo)
        plan = _make_plan(status=PlanStatus.APPROVED)
        plan.actions[0].status = ActionStatus.APPROVED

        result = await exec_svc.execute_plan(plan)

        assert result.status == PlanStatus.COMPLETED
        assert result.actions[0].status == ActionStatus.QUEUED
        assert result.actions[0].job_id == "job_new"
        posts_repo.create.assert_called_once()
        job_repo.enqueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_handles_partial_failure(self):
        posts_repo = AsyncMock()
        job_repo = AsyncMock()
        posts_repo.create.side_effect = Exception("DB error")

        exec_svc = PlanExecutionService(posts_repo, job_repo)
        plan = _make_plan(status=PlanStatus.APPROVED)
        plan.actions[0].status = ActionStatus.APPROVED

        result = await exec_svc.execute_plan(plan)

        assert result.status == PlanStatus.PARTIALLY_FAILED
        assert result.actions[0].status == ActionStatus.FAILED


# ── Mock factories ────────────────────────────────────────────────────────────

def _mock_content_service() -> ContentGenerationService:
    """Content service with mocked AI client that returns valid JSON."""
    client = _mock_ai_client('{"content": "Generated post content", "hashtags": ["test"]}')
    return ContentGenerationService(ai_client=client)


def _mock_plan_repo():
    """Mock plan repository that stores in-memory for tests."""
    store: dict = {}

    async def save(plan):
        store[plan.id] = plan
        return plan.id

    async def find_by_id(plan_id, tenant_id):
        p = store.get(plan_id)
        if p and p.tenant_id == tenant_id:
            return p
        return None

    async def find_by_tenant(tenant_id, limit=50, offset=0):
        return [p for p in store.values() if p.tenant_id == tenant_id]

    repo = AsyncMock()
    repo.save = save
    repo.find_by_id = find_by_id
    repo.find_by_tenant = find_by_tenant
    return repo


def _make_plan_service(content_service=None):
    """Create a PlanService with mocked dependencies for tests."""
    return PlanService(
        content_service=content_service or _mock_content_service(),
        plan_repo=_mock_plan_repo(),
    )


def _mock_ai_client(response_text: str):
    """Create a mock AI client that returns a fixed response."""
    client = AsyncMock()
    mock_choice = AsyncMock()
    mock_choice.message.content = response_text
    mock_response = AsyncMock()
    mock_response.choices = [mock_choice]
    client.chat.completions.create.return_value = mock_response
    return client
