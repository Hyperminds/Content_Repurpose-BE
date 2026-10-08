"""Service for social post lifecycle management.

Handles creation, scheduling, state transitions, and validation. Enforces
tenant isolation and the valid state machine defined in the domain layer.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

from app.social_publishing.domain.enums import PostStatus, SocialPlatform, VALID_TRANSITIONS
from app.social_publishing.domain.exceptions import (
    AccountNotFound,
    InvalidStateTransition,
    PlatformNotSupported,
    PostNotFound,
    SchedulingError,
    ValidationError,
)
from app.social_publishing.domain.models import SocialPost
from app.social_publishing.repositories.social_accounts_repository import SocialAccountsRepository
from app.social_publishing.repositories.social_posts_repository import SocialPostsRepository
from app.services.logger import log

# Minimum lead time for scheduling (prevents scheduling in the past)
MIN_SCHEDULE_LEAD_SECONDS = 60


class SocialPostsService:
    """Business logic for the social post lifecycle."""

    def __init__(
        self,
        posts_repo: SocialPostsRepository,
        accounts_repo: SocialAccountsRepository,
    ) -> None:
        self._posts = posts_repo
        self._accounts = accounts_repo

    # ── Creation ──────────────────────────────────────────────────────────────

    async def create_post(
        self,
        tenant_id: str,
        account_id: str,
        platform: str,
        content: str,
        media_urls: Optional[list[str]] = None,
        scheduled_at: Optional[datetime] = None,
    ) -> SocialPost:
        """Create a new social post in DRAFT or SCHEDULED status."""
        validated_platform = _validate_platform(platform)
        _validate_content(content)

        # Verify account exists and belongs to tenant
        account = await self._accounts.find_by_id(account_id, tenant_id)
        if not account:
            raise AccountNotFound(account_id)

        # Determine initial status
        status = PostStatus.DRAFT
        if scheduled_at:
            _validate_schedule_time(scheduled_at)
            status = PostStatus.SCHEDULED

        post = await self._posts.create(
            tenant_id=tenant_id,
            account_id=account_id,
            platform=validated_platform,
            content=content,
            media_urls=media_urls,
            scheduled_at=scheduled_at,
            status=status,
        )

        log.info(
            "Social post created",
            post_id=post.id,
            tenant_id=tenant_id,
            platform=platform,
            status=status.value,
        )
        return post

    # ── Queries ───────────────────────────────────────────────────────────────

    async def get_post(self, post_id: str, tenant_id: str) -> SocialPost:
        """Get a post by id, enforcing tenant ownership."""
        post = await self._posts.find_by_id(post_id, tenant_id)
        if not post:
            raise PostNotFound(post_id)
        return post

    async def list_posts(
        self,
        tenant_id: str,
        status: Optional[str] = None,
        platform: Optional[str] = None,
        account_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SocialPost]:
        """List posts for a tenant with optional filters."""
        validated_status = PostStatus(status) if status else None
        validated_platform = _validate_platform(platform) if platform else None

        return await self._posts.find_by_tenant(
            tenant_id=tenant_id,
            status=validated_status,
            platform=validated_platform,
            account_id=account_id,
            limit=limit,
            offset=offset,
        )

    # ── Updates ───────────────────────────────────────────────────────────────

    async def update_post(
        self,
        post_id: str,
        tenant_id: str,
        content: Optional[str] = None,
        media_urls: Optional[list[str]] = None,
        scheduled_at: Optional[datetime] = None,
    ) -> SocialPost:
        """Update a post (only in DRAFT or SCHEDULED states)."""
        post = await self.get_post(post_id, tenant_id)

        if post.status not in (PostStatus.DRAFT, PostStatus.SCHEDULED):
            raise InvalidStateTransition(
                post.status.value, "Cannot edit a post that is already in processing"
            )

        updates: dict = {}
        if content is not None:
            _validate_content(content)
            updates["content"] = content
        if media_urls is not None:
            updates["media_urls"] = media_urls
        if scheduled_at is not None:
            _validate_schedule_time(scheduled_at)
            updates["scheduled_at"] = scheduled_at

        if not updates:
            return post

        result = await self._posts.update_fields(post_id, tenant_id, updates)
        if not result:
            raise PostNotFound(post_id)
        return result

    # ── State transitions ─────────────────────────────────────────────────────

    async def schedule_post(self, post_id: str, tenant_id: str, scheduled_at: datetime) -> SocialPost:
        """Move a DRAFT post to SCHEDULED."""
        post = await self.get_post(post_id, tenant_id)
        _assert_transition(post.status, PostStatus.SCHEDULED)
        _validate_schedule_time(scheduled_at)

        result = await self._posts.update_status(
            post_id, tenant_id, PostStatus.SCHEDULED,
            extra_fields={"scheduled_at": scheduled_at},
        )
        log.info("Post scheduled", post_id=post_id, scheduled_at=scheduled_at.isoformat())
        return result

    async def cancel_post(self, post_id: str, tenant_id: str) -> SocialPost:
        """Cancel a post (from DRAFT, SCHEDULED, FAILED, or RETRYING)."""
        post = await self.get_post(post_id, tenant_id)
        _assert_transition(post.status, PostStatus.CANCELLED)

        result = await self._posts.update_status(post_id, tenant_id, PostStatus.CANCELLED)
        log.info("Post cancelled", post_id=post_id, previous_status=post.status.value)
        return result

    async def delete_post(self, post_id: str, tenant_id: str) -> bool:
        """Delete a post (only DRAFT or CANCELLED)."""
        post = await self.get_post(post_id, tenant_id)
        if post.status not in (PostStatus.DRAFT, PostStatus.CANCELLED):
            raise InvalidStateTransition(
                post.status.value, "Only draft or cancelled posts can be deleted"
            )
        success = await self._posts.delete(post_id, tenant_id)
        if success:
            log.info("Post deleted", post_id=post_id, tenant_id=tenant_id)
        return success


# ── Validation helpers ────────────────────────────────────────────────────────

def _validate_platform(platform: str) -> SocialPlatform:
    try:
        return SocialPlatform(platform.lower().strip())
    except ValueError:
        raise PlatformNotSupported(platform)


def _validate_content(content: str) -> None:
    if not content or not content.strip():
        raise ValidationError("Post content is required")


def _validate_schedule_time(scheduled_at: datetime) -> None:
    now = datetime.now(timezone.utc)
    # Ensure timezone-aware comparison
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    min_time = now + timedelta(seconds=MIN_SCHEDULE_LEAD_SECONDS)
    if scheduled_at < min_time:
        raise SchedulingError("Scheduled time must be at least 60 seconds in the future")


def _assert_transition(current: PostStatus, target: PostStatus) -> None:
    allowed = VALID_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransition(current.value, target.value)
