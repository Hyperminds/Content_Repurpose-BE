"""Publishing worker — claims and executes jobs from the queue.

The worker is a background asyncio loop that:
  1. Claims the next available job (atomic)
  2. Validates the job is still relevant
  3. Loads the social account and verifies its status
  4. Resolves the platform publisher
  5. Retrieves decrypted credentials
  6. Publishes via the publisher
  7. Handles success/failure and updates job + post status
  8. Releases resources

The worker contains NO platform-specific logic. It delegates all publishing
to the publisher returned by the registry.
"""

import asyncio
import time
from typing import Callable, Awaitable, Optional

from app.social_publishing.domain.enums import ConnectionStatus, PostStatus, SocialPlatform
from app.social_publishing.domain.models import PublishingResult, SocialPost
from app.social_publishing.jobs.models import FailureCategory, JobStatus, QueuedJob
from app.social_publishing.jobs.repository import JobRepository, generate_worker_id
from app.social_publishing.jobs.retry_policy import (
    classify_failure,
    compute_next_retry_at,
    is_retryable,
)
from app.social_publishing.publishers.registry import PublisherRegistry
from app.social_publishing.repositories.social_accounts_repository import SocialAccountsRepository
from app.social_publishing.repositories.social_posts_repository import SocialPostsRepository
from app.services.logger import log

# Type for credential resolver: (account_id, tenant_id) -> Optional[access_token]
CredentialResolver = Callable[[str, str], Awaitable[Optional[str]]]

# How long to sleep when no jobs are available (seconds)
_IDLE_SLEEP_SECONDS = 5

# How long to sleep between processing consecutive jobs (prevents CPU spin)
_BETWEEN_JOBS_SECONDS = 0.1


class PublishingWorker:
    """Background worker that claims and executes publishing jobs."""

    def __init__(
        self,
        job_repo: JobRepository,
        posts_repo: SocialPostsRepository,
        accounts_repo: SocialAccountsRepository,
        publisher_registry: PublisherRegistry,
        credential_resolver: CredentialResolver,
        metrics: Optional[object] = None,
        provider_router: Optional[object] = None,
    ) -> None:
        self._jobs = job_repo
        self._posts = posts_repo
        self._accounts = accounts_repo
        self._registry = publisher_registry
        self._resolve_credential = credential_resolver
        self._metrics = metrics
        # Optional hybrid-publishing router. When None, or when an account is a
        # native-API account, the worker uses the classic native path unchanged.
        self._provider_router = provider_router
        self._worker_id = generate_worker_id()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("PublishingWorker started", worker_id=self._worker_id)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        log.info("PublishingWorker stopped", worker_id=self._worker_id)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def worker_id(self) -> str:
        return self._worker_id

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while self._running:
            try:
                job = await self._jobs.claim_next(self._worker_id)
                if job:
                    await self._execute_job(job)
                    await asyncio.sleep(_BETWEEN_JOBS_SECONDS)
                else:
                    await asyncio.sleep(_IDLE_SLEEP_SECONDS)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("PublishingWorker loop error", worker_id=self._worker_id, error=str(e))
                await asyncio.sleep(_IDLE_SLEEP_SECONDS)

    # ── Job execution ─────────────────────────────────────────────────────────

    async def _execute_job(self, job: QueuedJob) -> None:
        """Execute a single claimed job through the full pipeline."""
        start_time = time.monotonic()

        # Step 1: Mark executing
        await self._jobs.mark_executing(job.id, self._worker_id)

        # Step 2: Load and validate post
        post = await self._posts.find_by_id_any_tenant(job.post_id)
        if not post:
            await self._fail_job(job, "Post no longer exists", FailureCategory.PERMANENT)
            return

        # Step 3: Load and verify account
        account = await self._accounts.find_by_id(job.account_id, job.tenant_id)
        if not account:
            await self._fail_job(job, "Social account not found", FailureCategory.PERMANENT)
            await self._update_post_failed(job, "Account deleted")
            return

        if account.connection_status == ConnectionStatus.DISCONNECTED:
            await self._fail_job(job, "Account disconnected", FailureCategory.PERMANENT)
            await self._update_post_failed(job, "Account disconnected")
            return

        if account.connection_status in (ConnectionStatus.REAUTH_REQUIRED, ConnectionStatus.TOKEN_EXPIRED):
            await self._fail_job(job, "Account requires re-authentication", FailureCategory.AUTH_EXPIRED)
            await self._update_post_status(job.post_id, PostStatus.FAILED, "Re-authentication required")
            return

        # Step 3b: Hybrid routing. If a router is configured and this account
        # uses a NON-native provider (Hermes / BYOK), execute through the
        # provider abstraction. Native-API accounts fall through to the classic
        # path below UNCHANGED.
        if self._provider_router is not None and _is_non_native(account):
            await self._execute_via_provider(job, post, account, start_time)
            return

        # Step 4: Resolve publisher
        publisher = self._registry.get_optional(job.platform)
        if not publisher:
            await self._fail_job(job, f"No publisher for {job.platform.value}", FailureCategory.PERMANENT)
            await self._update_post_failed(job, "Platform not supported")
            return

        # Step 5: Retrieve credentials
        access_token = await self._resolve_credential(job.account_id, job.tenant_id)
        if not access_token:
            await self._fail_job(job, "No valid credentials", FailureCategory.AUTH_EXPIRED)
            await self._update_post_failed(job, "Credentials unavailable")
            return

        # Step 6: Publish
        # Set the platform_account_id on the post so the publisher can build the correct author URN
        post.account_id = account.platform_account_id
        result = await self._safe_publish(publisher, post, access_token)

        # Step 7: Handle result
        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        if result.success:
            await self._complete_job(job, result, elapsed_ms)
        else:
            await self._handle_failure(job, result, elapsed_ms)

    # ── Hybrid provider execution (non-native accounts) ────────────────────────

    async def _execute_via_provider(self, job, post, account, start_time) -> None:
        """
        Execute a non-native account's job through the provider abstraction.

        Applies the capability pre-flight (Phase 2), normalized results, and the
        verify-before-retry / user-action policy (Phase 8/10/11). Never touches
        the native path.
        """
        from app.social_publishing.providers.operation import (
            instruction_from_job,
            decide_after_result,
        )
        from app.social_publishing.providers.capabilities import (
            requirements_for,
            unmet_requirements,
        )
        from app.social_publishing.providers.errors import ExternalResultStatus

        provider = self._provider_router.resolve(account)
        if provider is None:
            await self._fail_job(
                job, f"No provider registered for {account.provider_type.value}",
                FailureCategory.PERMANENT,
            )
            await self._update_post_failed(job, "Publishing method not available")
            return

        # Capability pre-flight — fail fast on unsupported content.
        caps = provider.capabilities(job.platform.value)
        reqs = requirements_for(post.content or "", post.media_urls or [], bool(post.scheduled_at))
        missing = unmet_requirements(caps, reqs)
        if missing:
            await self._fail_job(
                job, f"Provider cannot publish: {', '.join(missing)}",
                FailureCategory.PERMANENT,
            )
            await self._update_post_failed(job, f"Unsupported content: {', '.join(missing)}")
            return

        instruction = instruction_from_job(job, post, account)

        from app.social_publishing.providers.observability import emit, PublishEvent

        def _obs(event, result_label=None, reason=None):
            emit(
                event,
                tenant_id=job.tenant_id, job_id=job.id,
                operation_id=instruction.operation_id,
                provider=account.provider_name, platform=job.platform.value,
                account_id=account.id, result=result_label, reason=reason,
            )

        _obs(PublishEvent.STARTED)

        # Credentials only resolved for API-based providers; agents ignore it.
        access_token = await self._resolve_credential(job.account_id, job.tenant_id)

        try:
            result = await provider.publish(instruction, access_token)
        except Exception as e:
            # Never blind-fail: an exception mid-external-publish is UNKNOWN.
            log.error("Provider execution error", job_id=job.id, error=type(e).__name__)
            _obs(PublishEvent.UNKNOWN, "unknown", "provider execution error")
            await self._mark_job_unknown(job, "Provider execution error")
            return

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        decision = decide_after_result(result)

        if result.status == ExternalResultStatus.PUBLISHED:
            await self._jobs.mark_completed(job.id, self._worker_id, result.external_post_id)
            await self._update_post_published(job, result.external_post_id)
            from app.social_publishing.notifications import notify_publish_success
            await notify_publish_success(
                tenant_id=job.tenant_id, post_id=job.post_id,
                platform=job.platform.value, platform_post_id=result.external_post_id,
            )
            if self._metrics:
                self._metrics.record_success(elapsed_ms)
            _obs(PublishEvent.COMPLETED, "published")
            log.info("Provider job completed", job_id=job.id, provider=account.provider_name)
            return

        if decision.needs_user_action:
            # Pause for the user — NOT a failure. Job is not re-claimed.
            _obs(PublishEvent.ACTION_REQUIRED, "action_required")
            await self._mark_job_action_required(job, result.error_message or "Action required")
            return

        if decision.needs_verification:
            # UNKNOWN — never blind-retry. Mark for verification.
            _obs(PublishEvent.UNKNOWN, "unknown", result.error_message)
            await self._mark_job_unknown(job, result.error_message or "Outcome uncertain")
            return

        # Definite failure — honor the provider's retryable flag.
        if decision.should_retry:
            _obs(PublishEvent.FAILED, "failed_retryable", result.error_message)
            await self._handle_failure(
                job,
                PublishingResult(success=False, error_message=result.error_message, retryable=True),
                elapsed_ms,
            )
        else:
            await self._jobs.mark_failed(
                job.id, self._worker_id, result.error_message or "Failed",
                FailureCategory.PERMANENT,
            )
            await self._update_post_failed(job, result.error_message)
            from app.social_publishing.notifications import notify_publish_failed
            await notify_publish_failed(
                tenant_id=job.tenant_id, post_id=job.post_id,
                platform=job.platform.value, reason=result.error_message or "Failed",
                retryable=False,
            )
            if self._metrics:
                self._metrics.record_failure()
            _obs(PublishEvent.FAILED, "failed", result.error_message)

    async def _mark_job_unknown(self, job, reason: str) -> None:
        """Mark job + post UNKNOWN. UNKNOWN is never auto-claimed for retry."""
        from app.social_publishing.jobs.models import JobStatus
        await self._jobs.mark_failed(job.id, self._worker_id, reason, FailureCategory.UNKNOWN)
        # Job-queue: mark_failed with no next_retry leaves it terminal FAILED;
        # for UNKNOWN we set the post to UNKNOWN so it surfaces for verification.
        await self._update_post_status(job.post_id, PostStatus.UNKNOWN, reason)
        log.warning("Provider job UNKNOWN — verification required", job_id=job.id)

    async def _mark_job_action_required(self, job, reason: str) -> None:
        """Mark job + post ACTION_REQUIRED (user-assisted pause)."""
        await self._update_post_status(job.post_id, PostStatus.ACTION_REQUIRED, reason)
        log.info("Provider job needs user action", job_id=job.id)

    # ── Success handling ──────────────────────────────────────────────────────

    async def _complete_job(self, job: QueuedJob, result: PublishingResult, elapsed_ms: int) -> None:
        """Handle successful publish."""
        await self._jobs.mark_completed(job.id, self._worker_id, result.platform_post_id)
        await self._update_post_published(job, result.platform_post_id)

        # Notify tenant via WebSocket
        from app.social_publishing.notifications import notify_publish_success
        await notify_publish_success(
            tenant_id=job.tenant_id,
            post_id=job.post_id,
            platform=job.platform.value,
            platform_post_id=result.platform_post_id,
        )

        log.info(
            "Job completed",
            job_id=job.id,
            post_id=job.post_id,
            platform=job.platform.value,
            elapsed_ms=elapsed_ms,
        )

        if self._metrics:
            self._metrics.record_success(elapsed_ms)

    # ── Failure handling ──────────────────────────────────────────────────────

    async def _handle_failure(self, job: QueuedJob, result: PublishingResult, elapsed_ms: int) -> None:
        """Handle failed publish — classify and decide retry vs permanent fail."""
        category = classify_failure(result)
        retryable = is_retryable(category, job.attempts, job.max_attempts)

        if retryable:
            next_retry = compute_next_retry_at(job.attempts, category)
            await self._jobs.mark_failed(
                job.id, self._worker_id,
                reason=result.error_message or "Unknown",
                category=category,
                next_retry_at=next_retry,
            )
            await self._update_post_status(job.post_id, PostStatus.RETRYING, result.error_message)

            # Notify tenant
            from app.social_publishing.notifications import notify_publish_retrying
            await notify_publish_retrying(
                tenant_id=job.tenant_id, post_id=job.post_id,
                platform=job.platform.value, attempt=job.attempts, max_attempts=job.max_attempts,
            )

            log.warning(
                "Job failed — will retry",
                job_id=job.id,
                attempt=job.attempts,
                category=category.value,
                next_retry=next_retry.isoformat(),
            )
            if self._metrics:
                self._metrics.record_retry()
        else:
            await self._jobs.mark_failed(
                job.id, self._worker_id,
                reason=result.error_message or "Unknown",
                category=category,
            )
            await self._update_post_failed(job, result.error_message)

            # Notify tenant
            from app.social_publishing.notifications import notify_publish_failed
            await notify_publish_failed(
                tenant_id=job.tenant_id, post_id=job.post_id,
                platform=job.platform.value, reason=result.error_message or "Unknown",
                retryable=False,
            )

            log.error(
                "Job permanently failed",
                job_id=job.id,
                post_id=job.post_id,
                category=category.value,
                reason=result.error_message,
            )
            if self._metrics:
                self._metrics.record_failure()

    async def _fail_job(self, job: QueuedJob, reason: str, category: FailureCategory) -> None:
        """Shortcut: mark a job as permanently failed (no retry)."""
        await self._jobs.mark_failed(job.id, self._worker_id, reason, category)
        log.warning("Job failed early", job_id=job.id, reason=reason)
        if self._metrics:
            self._metrics.record_failure()

    # ── Post status updates ───────────────────────────────────────────────────

    async def _update_post_published(self, job: QueuedJob, platform_post_id: Optional[str]) -> None:
        from datetime import datetime, timezone
        await self._posts.update_status_by_id(
            job.post_id,
            PostStatus.PUBLISHED,
            extra_fields={
                "published_at": datetime.now(timezone.utc),
                "platform_post_id": platform_post_id,
                "failure_reason": None,
            },
        )

    async def _update_post_failed(self, job: QueuedJob, reason: Optional[str]) -> None:
        await self._update_post_status(job.post_id, PostStatus.FAILED, reason)

    async def _update_post_status(self, post_id: str, status: PostStatus, reason: Optional[str] = None) -> None:
        extra: dict = {}
        if reason:
            extra["failure_reason"] = reason
        if status == PostStatus.RETRYING:
            extra["retry_count"] = {"$inc": 1}  # won't work in $set — handle below
        await self._posts.update_status_by_id(post_id, status, extra_fields=extra if extra else None)

    # ── Safe publish execution ────────────────────────────────────────────────

    async def _safe_publish(self, publisher, post: SocialPost, access_token: str) -> PublishingResult:
        """Call the publisher, catching any unhandled exception."""
        try:
            return await publisher.publish(post, access_token)
        except Exception as e:
            return PublishingResult(
                success=False,
                error_message=f"Publisher exception: {type(e).__name__}: {str(e)[:200]}",
                retryable=True,
            )


def _is_non_native(account) -> bool:
    """
    True if the account uses a non-native (Hermes/BYOK) provider.

    Native-API accounts (the default, and every legacy account) return False so
    they always take the classic publishing path.
    """
    from app.social_publishing.domain.enums import ProviderType
    return account.provider_type != ProviderType.NATIVE_API
