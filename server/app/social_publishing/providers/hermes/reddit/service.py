"""Reddit user-assisted publishing service.

Orchestrates the Reddit workflow across the two user-assisted phases, owning:
  - the browser adapter lifecycle (start/close) per request
  - the pending-confirmation persistence (same operation_id throughout)
  - audit events
  - tenant/account scoping

It does NOT contain browser code (that's PlaywrightHermesAdapter) and does NOT
contain Reddit page logic (that's HermesRedditWorkflow). It is the glue the API
routes call.

Because a browser page's in-memory composer form does not survive a fresh
browser launch, the confirm phase re-runs prepare() (idempotent — same
operation_id) and then submits, all within ONE browser session in ONE request.
Login persists across requests via the on-disk persistent profile, so the user
is not asked to log in again.
"""

from typing import Optional

from app.services.logger import log
from app.social_publishing.providers.errors import (
    ExternalResultStatus,
    PublishInstruction,
)
from app.social_publishing.providers.observability import emit, PublishEvent
from app.social_publishing.providers.hermes.playwright_adapter import PlaywrightHermesAdapter
from app.social_publishing.providers.hermes.reddit import constants as C
from app.social_publishing.providers.hermes.reddit.validation import validate_post
from app.social_publishing.providers.hermes.reddit.workflow import HermesRedditWorkflow
from app.social_publishing.providers.hermes.pending_confirmation_repository import (
    PendingConfirmationRepository,
)
from app.social_publishing.providers.hermes.media_workspace import media_workspace

# Adapter factory is injectable so tests can supply a scripted SemanticBrowser.
def _default_adapter_factory(profile_key: str):
    return PlaywrightHermesAdapter(profile_key)


async def _default_username_resolver(account_id: str, tenant_id: str) -> str:
    """Resolve a Reddit account's username (platform_account_id), tenant-scoped.

    Lazy import keeps this module importable without the DB layer in tests.
    Returns "" if the account can't be found — verification simply skips the
    submitted-posts fallback in that case.
    """
    from app.social_publishing.repositories.social_accounts_repository import (
        SocialAccountsRepository,
    )
    account = await SocialAccountsRepository().find_by_id(account_id, tenant_id)
    return (account.platform_account_id or "") if account else ""


class RedditUserAssistedService:
    """Coordinates prepare / confirm / cancel for Reddit user-assisted posts."""

    def __init__(
        self,
        pending_repo: Optional[PendingConfirmationRepository] = None,
        workflow: Optional[HermesRedditWorkflow] = None,
        adapter_factory=None,
        username_resolver=None,
    ) -> None:
        self._pending = pending_repo or PendingConfirmationRepository()
        self._workflow = workflow or HermesRedditWorkflow()
        self._adapter_factory = adapter_factory or _default_adapter_factory
        # Resolves a Reddit username (account.platform_account_id) for an
        # (account_id, tenant_id). Injectable so tests avoid the DB. The username
        # is used ONLY as a read-only verification fallback (look up the user's
        # submitted posts when the post-submit URL doesn't transition).
        self._username_resolver = username_resolver or _default_username_resolver

    def _profile_key(self, tenant_id: str, account_id: str) -> str:
        return f"{tenant_id}_{account_id}"

    async def _resolve_username(self, account_id: str, tenant_id: str) -> str:
        try:
            return await self._username_resolver(account_id, tenant_id) or ""
        except Exception:
            # Verification fallback is best-effort; never block publishing on it.
            return ""

    def _instruction(
        self, tenant_id: str, account_id: str, operation_id: str,
        title: str, body: str, subreddit: str, media_paths: list[str],
        username: str = "",
    ) -> PublishInstruction:
        return PublishInstruction(
            operation_id=operation_id,
            tenant_id=tenant_id,
            account_id=account_id,
            platform=C.PLATFORM,
            provider_name="hermes",
            text=body,
            media_urls=[],
            meta={
                "reddit_title": title,
                "reddit_subreddit": subreddit,
                "reddit_media_paths": media_paths,
                "reddit_username": username,
                "account_record_id": account_id,
            },
        )

    # ── Prepare (stops at ACTION_REQUIRED) ──────────────────────────────────────

    async def prepare(
        self, tenant_id: str, account_id: str, operation_id: str,
        title: str, body: str, subreddit: str, media_paths: Optional[list[str]] = None,
    ) -> dict:
        """
        Validate + prepare a Reddit post, stopping for user confirmation.

        Returns a status dict. On ACTION_REQUIRED, persists a pending
        confirmation keyed by operation_id.
        """
        media_paths = media_paths or []
        parsed, errors = validate_post(title, body, subreddit, media_paths)
        if errors:
            return {"status": "failed", "errors": errors}

        emit(
            PublishEvent.STARTED, tenant_id=tenant_id, job_id=operation_id,
            operation_id=operation_id, provider="hermes", platform=C.PLATFORM,
            account_id=account_id, result="preparing",
        )

        profile_key = self._profile_key(tenant_id, account_id)
        username = await self._resolve_username(account_id, tenant_id)
        instruction = self._instruction(
            tenant_id, account_id, operation_id,
            parsed.title, parsed.body, parsed.subreddit, media_paths,
            username=username,
        )

        adapter = self._adapter_factory(profile_key)
        try:
            await _maybe_start(adapter)
            result = await self._workflow.prepare(instruction, adapter)
        finally:
            await _maybe_close(adapter)

        if result.status == ExternalResultStatus.ACTION_REQUIRED:
            pending = await self._pending.create(
                tenant_id=tenant_id, account_id=account_id, operation_id=operation_id,
                platform=C.PLATFORM, provider_name="hermes",
                title=parsed.title, body=parsed.body, subreddit=parsed.subreddit,
                media_paths=media_paths, session_profile_key=profile_key,
            )
            emit(
                PublishEvent.ACTION_REQUIRED, tenant_id=tenant_id, job_id=operation_id,
                operation_id=operation_id, provider="hermes", platform=C.PLATFORM,
                account_id=account_id, result="action_required",
            )
            return {
                "status": "action_required",
                "action": C.CONFIRM_ACTION,
                "message": result.error_message or "Your Reddit post is ready. Confirm publishing.",
                "confirmation_id": pending.id,
                "operation_id": operation_id,
                "preview": {
                    "subreddit": parsed.subreddit,
                    "title": parsed.title,
                    "body": parsed.body,
                    "media_count": len(media_paths),
                },
            }

        # Not action_required → failure (e.g. validation/browser/subreddit)
        emit(
            PublishEvent.FAILED, tenant_id=tenant_id, job_id=operation_id,
            operation_id=operation_id, provider="hermes", platform=C.PLATFORM,
            account_id=account_id, result="failed", reason=result.error_message,
        )
        return {"status": "failed", "message": result.error_message}

    # ── Confirm (explicit user action → submit → verify) ────────────────────────

    async def confirm(self, tenant_id: str, confirmation_id: str) -> dict:
        """
        Resume the SAME operation and publish, only after explicit confirmation.

        Re-prepares (idempotent, same operation_id) then submits in one session.
        UNKNOWN outcomes are surfaced (never blind-retried).
        """
        pending = await self._pending.find(confirmation_id, tenant_id)
        if not pending:
            return {"status": "not_found"}
        if pending.status not in ("action_required", "waiting_for_user"):
            return {"status": pending.status, "message": "No longer awaiting confirmation"}

        await self._pending.set_status(confirmation_id, tenant_id, "waiting_for_user")

        username = await self._resolve_username(pending.account_id, tenant_id)
        instruction = self._instruction(
            tenant_id, pending.account_id, pending.operation_id,
            pending.title, pending.body, pending.subreddit, pending.media_paths,
            username=username,
        )
        adapter = self._adapter_factory(pending.session_profile_key)

        try:
            await _maybe_start(adapter)
            # Re-fill the composer in this fresh session (login persists on disk).
            prep = await self._workflow.prepare(instruction, adapter)
            if prep.status != ExternalResultStatus.ACTION_REQUIRED:
                await self._pending.set_status(confirmation_id, tenant_id, "failed")
                return {"status": "failed", "message": prep.error_message}

            emit(
                PublishEvent.VERIFYING, tenant_id=tenant_id, job_id=pending.operation_id,
                operation_id=pending.operation_id, provider="hermes", platform=C.PLATFORM,
                account_id=pending.account_id, result="submitting",
            )
            result = await self._workflow.confirm_and_publish(instruction, adapter)
        finally:
            await _maybe_close(adapter)

        if result.status == ExternalResultStatus.PUBLISHED:
            await self._pending.set_status(
                confirmation_id, tenant_id, "published", external_url=result.external_url
            )
            emit(
                PublishEvent.COMPLETED, tenant_id=tenant_id, job_id=pending.operation_id,
                operation_id=pending.operation_id, provider="hermes", platform=C.PLATFORM,
                account_id=pending.account_id, result="published",
            )
            return {
                "status": "published",
                "external_url": result.external_url,
                "external_post_id": result.external_post_id,
                "platform": C.PLATFORM,
            }

        if result.status == ExternalResultStatus.UNKNOWN:
            await self._pending.set_status(confirmation_id, tenant_id, "unknown")
            emit(
                PublishEvent.UNKNOWN, tenant_id=tenant_id, job_id=pending.operation_id,
                operation_id=pending.operation_id, provider="hermes", platform=C.PLATFORM,
                account_id=pending.account_id, result="unknown", reason=result.error_message,
            )
            return {"status": "unknown", "message": result.error_message}

        await self._pending.set_status(confirmation_id, tenant_id, "failed")
        emit(
            PublishEvent.FAILED, tenant_id=tenant_id, job_id=pending.operation_id,
            operation_id=pending.operation_id, provider="hermes", platform=C.PLATFORM,
            account_id=pending.account_id, result="failed", reason=result.error_message,
        )
        return {"status": "failed", "message": result.error_message}

    # ── Cancel ──────────────────────────────────────────────────────────────────

    async def cancel(self, tenant_id: str, confirmation_id: str) -> dict:
        """Cancel a pending confirmation without publishing (Phase 11)."""
        pending = await self._pending.find(confirmation_id, tenant_id)
        if not pending:
            return {"status": "not_found"}
        await self._pending.set_status(confirmation_id, tenant_id, "cancelled")
        log.info("Reddit publish cancelled", op=pending.operation_id, tenant=tenant_id)
        return {"status": "cancelled"}

    async def list_pending(self, tenant_id: str) -> list[dict]:
        items = await self._pending.list_awaiting(tenant_id)
        return [
            {
                "confirmation_id": p.id,
                "operation_id": p.operation_id,
                "platform": p.platform,
                "status": p.status,
                "preview": {
                    "subreddit": p.subreddit,
                    "title": p.title,
                    "body": p.body,
                    "media_count": len(p.media_paths),
                },
                "expires_at": p.expires_at.isoformat() if p.expires_at else None,
            }
            for p in items
        ]


async def _maybe_start(adapter) -> None:
    """Call start() if the adapter exposes it (real adapter); no-op otherwise."""
    start = getattr(adapter, "start", None)
    if callable(start):
        await start()


async def _maybe_close(adapter) -> None:
    close = getattr(adapter, "close", None)
    if callable(close):
        await close()
