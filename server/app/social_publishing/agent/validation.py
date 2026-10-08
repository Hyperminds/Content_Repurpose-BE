"""Plan validation — validates all agent output before execution.

The AI agent produces structured data, but LLM output cannot be blindly trusted.
This module validates every field before a plan can be approved or executed.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.social_publishing.agent.models import (
    ActionStatus,
    ApprovalMode,
    ContentVariant,
    PlanStatus,
    PublishingPlan,
    ScheduledAction,
)
from app.social_publishing.domain.enums import SocialPlatform

# Supported platforms (must match what Trendzzo has publishers for)
_SUPPORTED_PLATFORMS = {p.value for p in SocialPlatform}

# Content limits per platform
_MAX_CONTENT_LENGTH: dict[str, int] = {
    "linkedin": 3000,
    "instagram": 2200,
    "meta": 63206,
    "twitter": 280,
    "threads": 500,
    "reddit": 40000,
    "medium": 100000,
    "quora": 100000,
}

# Schedule constraints
MIN_SCHEDULE_LEAD_MINUTES = 5
MAX_SCHEDULE_DAYS_AHEAD = 90


def validate_plan(plan: PublishingPlan, available_accounts: set[str]) -> list[str]:
    """
    Validate a complete publishing plan.

    Returns a list of error messages. Empty list = valid.

    Args:
        plan: The plan to validate.
        available_accounts: Set of account IDs the tenant actually owns.
    """
    errors: list[str] = []

    if not plan.title or not plan.title.strip():
        errors.append("Plan must have a title")

    if not plan.actions:
        errors.append("Plan must have at least one action")
        return errors

    for i, action in enumerate(plan.actions):
        action_errors = validate_action(action, available_accounts, index=i)
        errors.extend(action_errors)

    return errors


def validate_action(
    action: ScheduledAction,
    available_accounts: set[str],
    index: int = 0,
) -> list[str]:
    """Validate a single scheduled action."""
    errors: list[str] = []
    prefix = f"Action #{index + 1}"

    # Platform check
    if action.platform not in _SUPPORTED_PLATFORMS:
        errors.append(f"{prefix}: Unsupported platform '{action.platform}'")

    # Account ownership
    if action.account_id not in available_accounts:
        errors.append(f"{prefix}: Account '{action.account_id}' not available for this tenant")

    # Content validation
    content_errors = validate_content_variant(action.content_variant, action.platform, prefix)
    errors.extend(content_errors)

    # Schedule validation
    schedule_errors = validate_schedule_time(action.scheduled_at, prefix)
    errors.extend(schedule_errors)

    return errors


def validate_content_variant(
    variant: ContentVariant,
    platform: str,
    prefix: str = "",
) -> list[str]:
    """Validate a content variant's text and metadata."""
    errors: list[str] = []

    if not variant.content or not variant.content.strip():
        errors.append(f"{prefix}: Content cannot be empty")
        return errors

    # Platform-specific length check
    max_length = _MAX_CONTENT_LENGTH.get(platform, 10000)
    full_text = variant.full_content
    if len(full_text) > max_length:
        errors.append(
            f"{prefix}: Content exceeds {platform} limit "
            f"({len(full_text)}/{max_length} chars)"
        )

    # Hashtag validation (no special characters, reasonable length)
    for tag in variant.hashtags:
        if not tag or len(tag) > 100:
            errors.append(f"{prefix}: Invalid hashtag '{tag[:20]}...'")
        if " " in tag:
            errors.append(f"{prefix}: Hashtag cannot contain spaces: '{tag}'")

    return errors


def validate_schedule_time(scheduled_at: datetime, prefix: str = "") -> list[str]:
    """Validate that a scheduled time is reasonable."""
    errors: list[str] = []
    now = datetime.now(timezone.utc)

    # Ensure timezone-aware
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

    min_time = now + timedelta(minutes=MIN_SCHEDULE_LEAD_MINUTES)
    max_time = now + timedelta(days=MAX_SCHEDULE_DAYS_AHEAD)

    if scheduled_at < min_time:
        errors.append(f"{prefix}: Scheduled time must be at least {MIN_SCHEDULE_LEAD_MINUTES} minutes in the future")

    if scheduled_at > max_time:
        errors.append(f"{prefix}: Scheduled time cannot be more than {MAX_SCHEDULE_DAYS_AHEAD} days ahead")

    return errors
