"""Publishing orchestrator — coordinates the act of publishing a post.

Responsible for:
  1. Transitioning post through QUEUED → PUBLISHING → PUBLISHED/FAILED
  2. Looking up the correct publisher from the registry
  3. Retrieving credentials (via token callback)
  4. Creating audit trail (PublishingJob records)
  5. Handling retries

The orchestrator is platform-agnostic. It delegates platform-specific work to
the publisher returned by the registry.
"""

from datetime import datetime, timezone
from typing import Callable, Awaitable, Optional

from app.social_publishing.domain.enums import PostStatus, VALID_TRANSITIONS
from app.social_publishing.domain.exceptions import (
    PostNotFound,
    PlatformNotSupported,
    PublishingFailed,
)
from app.social_publishing.domain.models import SocialPost, PublishingResult
from app.social_publishing.publishers.registry import PublisherRegistry
from app.social_publishing.repositories.social_posts_repository import SocialPostsRepository
from app.social_publishing.repositories.publishing_jobs_repository import PublishingJobsRepository
from app.services.logger import log

# Type alias for the token retrieval callback.
# Signature: (account_id: str, tenant_id: str) -> Optional[str]
TokenResolver = Callable[[str, str], Awaitable[Optional[str]]]

# Maximum retry attempts before giving up permanently
MAX_RETRY_ATTEMPTS = 5


class PublishingOrchestrator:
    """Coordinates publishing a single post through the full lifecycle."""

    def __init__(
        self,
        posts_repo: SocialPostsRepository,
        jobs_repo: PublishingJobsRepository,
        publisher_registry: PublisherRegistry,
        token_resolver: Optional[TokenResolver] = None,
    ) -> None:
        self._posts = posts_repo
        self._jobs = jobs_repo
        self._registry = publisher_registry
        self._resolve_token = token_resolver or _default_token_resolver

    async def publish_post(self, post_id: str) -> PublishingResult:
        """
        Execute the full publishing flow for a single post.

        Called by the scheduler or directly for immediate publishing.
        This is the single entry point — callers just pass a post_id.
        """
        post = await self._posts.find_by_id_any_tenant(post_id)
        if not post:
            raise PostNotFound(post_id)

        # Ensure post is in a publishable state
        if post.status not in (PostStatus.QUEUED, PostStatus.RETRYING):
            log.warning(
                "Orchestrator received post in unexpected state",
                post_id=post_id,
                status=post.status.value,
            )
            return PublishingResult(success=False, error_message=f"Post in non-publishable state: {post.status.value}")

        # Transition to PUBLISHING
        await self._posts.update_status_by_id(post_id, PostStatus.PUBLISHING)

        # Resolve publisher
        publisher = self._registry.get_optional(post.platform)
        if not publisher:
            await self._mark_failed(post, "No publisher registered for platform")
            return PublishingResult(success=False, error_message="No publisher available", retryable=False)

        # Resolve access token
        access_token = await self._resolve_token(post.account_id, post.tenant_id)
        if not access_token:
            await self._mark_failed(post, "No valid access token available")
            return PublishingResult(success=False, error_message="No access token", retryable=True)

        # Create job record
        job = await self._jobs.create(
            tenant_id=post.tenant_id,
            post_id=post.id,
            platform=post.platform,
            attempt_number=post.retry_count + 1,
        )

        # Execute publish
        try:
            result = await publisher.publish(post, access_token)
        except Exception as e:
            error_msg = f"Publisher raised exception: {type(e).__name__}"
            log.error("Publishing exception", post_id=post_id, error=str(e))
            await self._jobs.mark_failed(job.id, error_msg)
            await self._mark_failed(post, error_msg)
            return PublishingResult(success=False, error_message=error_msg, retryable=True)

        # Process result
        if result.success:
            await self._mark_published(post, result.platform_post_id)
            await self._jobs.mark_completed(job.id, result.platform_post_id)
            log.post_published(post.platform.value, result.platform_post_id or "", post.tenant_id)
            return result

        # Failed
        await self._jobs.mark_failed(job.id, result.error_message or "Unknown error")
        if result.retryable and post.retry_count < MAX_RETRY_ATTEMPTS:
            await self._mark_retrying(post, result.error_message)
        else:
            await self._mark_failed(post, result.error_message or "Max retries exhausted")

        return result

    # ── Internal state management ─────────────────────────────────────────────

    async def _mark_published(self, post: SocialPost, platform_post_id: Optional[str]) -> None:
        await self._posts.update_status_by_id(
            post.id,
            PostStatus.PUBLISHED,
            extra_fields={
                "published_at": datetime.now(timezone.utc),
                "platform_post_id": platform_post_id,
                "failure_reason": None,
            },
        )

    async def _mark_failed(self, post: SocialPost, reason: Optional[str]) -> None:
        await self._posts.update_status_by_id(
            post.id,
            PostStatus.FAILED,
            extra_fields={"failure_reason": reason},
        )
        log.post_failed(post.platform.value, reason or "Unknown", post.tenant_id)

    async def _mark_retrying(self, post: SocialPost, reason: Optional[str]) -> None:
        await self._posts.update_status_by_id(
            post.id,
            PostStatus.RETRYING,
            extra_fields={
                "retry_count": post.retry_count + 1,
                "failure_reason": reason,
            },
        )
        log.warning(
            "Post marked for retry",
            post_id=post.id,
            attempt=post.retry_count + 1,
            reason=reason,
        )


async def _default_token_resolver(account_id: str, tenant_id: str) -> Optional[str]:
    """
    Default token resolver — reads from the oauth token store.
    Can be replaced in tests with a mock.
    """
    try:
        from app.social_publishing.repositories.social_accounts_repository import SocialAccountsRepository
        repo = SocialAccountsRepository()
        account = await repo.find_by_id(account_id, tenant_id)
        if account and account.has_access_token:
            # In production, this would call the token_store to get the actual token.
            # For now, return a placeholder that signals "credentials exist."
            from app.services.oauth.token_store import get_valid_token
            token_data = await get_valid_token(tenant_id, account.platform.value, account_id)
            return token_data["access_token"] if token_data else None
    except Exception:
        pass
    return None
