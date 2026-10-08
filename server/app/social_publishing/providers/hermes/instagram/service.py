"""Instagram user-assisted publishing service.

Coordinates prepare / confirm / cancel for Instagram user-assisted posts,
mirroring RedditUserAssistedService. Owns:
  - the browser adapter lifecycle (start/close) per request
  - the pending-confirmation persistence (same operation_id throughout)
  - audit events
  - tenant/account scoping (isolated persistent browser profile per account)

It contains NO browser code (PlaywrightHermesAdapter) and NO Instagram page
logic (HermesInstagramWorkflow). Login persists across requests via the on-disk
persistent profile, so the confirm phase re-prepares (idempotent) then submits
within ONE browser session.
"""

from typing import Optional

from app.services.logger import log
from app.social_publishing.providers.errors import (
    ExternalResultStatus,
    PublishInstruction,
)
from app.social_publishing.providers.observability import emit, PublishEvent
from app.social_publishing.providers.hermes.playwright_adapter import PlaywrightHermesAdapter
from app.social_publishing.providers.hermes.instagram import constants as C
from app.social_publishing.providers.hermes.instagram.validation import validate_post
from app.social_publishing.providers.hermes.instagram.workflow import HermesInstagramWorkflow
from app.social_publishing.providers.hermes.pending_confirmation_repository import (
    PendingConfirmationRepository,
)


def _default_adapter_factory(profile_key: str):
    return PlaywrightHermesAdapter(profile_key)


async def _default_username_resolver(account_id: str, tenant_id: str) -> str:
    """Resolve an Instagram account's username (platform_account_id), tenant-scoped."""
    from app.social_publishing.repositories.social_accounts_repository import (
        SocialAccountsRepository,
    )
    account = await SocialAccountsRepository().find_by_id(account_id, tenant_id)
    return (account.platform_account_id or "") if account else ""


class InstagramUserAssistedService:
    """Coordinates prepare / confirm / cancel for Instagram user-assisted posts."""

    def __init__(
        self,
        pending_repo: Optional[PendingConfirmationRepository] = None,
        workflow: Optional[HermesInstagramWorkflow] = None,
        adapter_factory=None,
        username_resolver=None,
    ) -> None:
        self._pending = pending_repo or PendingConfirmationRepository()
        self._workflow = workflow or HermesInstagramWorkflow()
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._username_resolver = username_resolver or _default_username_resolver

    def _profile_key(self, tenant_id: str, account_id: str) -> str:
        # Per-account, per-tenant isolated persistent browser profile. Namespaced
        # with the platform so an Instagram profile never collides with a Reddit
        # one for the same account id.
        return f"ig_{tenant_id}_{account_id}"

    async def _resolve_username(self, account_id: str, tenant_id: str) -> str:
        try:
            return await self._username_resolver(account_id, tenant_id) or ""
        except Exception:
            return ""

    def _instruction(
        self, tenant_id: str, account_id: str, operation_id: str,
        caption: str, media_paths: list[str], username: str = "",
    ) -> PublishInstruction:
        return PublishInstruction(
            operation_id=operation_id,
            tenant_id=tenant_id,
            account_id=account_id,
            platform=C.PLATFORM,
            provider_name="hermes",
            text=caption,
            media_urls=[],
            meta={
                "instagram_caption": caption,
                "instagram_media_paths": media_paths,
                "instagram_username": username,
                "account_record_id": account_id,
            },
        )

    # ── Prepare (stops at ACTION_REQUIRED) ──────────────────────────────────────

    async def prepare(
        self, tenant_id: str, account_id: str, operation_id: str,
        caption: str, media_paths: Optional[list[str]] = None,
    ) -> dict:
        media_paths = media_paths or []
        parsed, errors = validate_post(caption, media_paths)
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
            tenant_id, account_id, operation_id, parsed.caption, media_paths, username=username,
        )

        # IMMEDIATE-PUBLISH MODEL (uniform across all Hermes platforms): prepare
        # opens its OWN browser, builds the composer to confirm the post is ready,
        # then CLOSES it. The browser is NOT kept alive. On confirm we always open
        # a fresh browser and re-prepare, so a second launch at publish time is
        # expected and intentional — this matches Reddit and keeps every platform
        # identical.
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
                title="", body=parsed.caption, subreddit="",
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
                "message": result.error_message or "Your Instagram post is ready. Confirm publishing.",
                "confirmation_id": pending.id,
                "operation_id": operation_id,
                "preview": {
                    "caption": parsed.caption,
                    "media_count": len(media_paths),
                },
            }

        emit(
            PublishEvent.FAILED, tenant_id=tenant_id, job_id=operation_id,
            operation_id=operation_id, provider="hermes", platform=C.PLATFORM,
            account_id=account_id, result="failed", reason=result.error_message,
        )
        return {"status": "failed", "message": result.error_message}

    # ── Confirm (explicit user action → submit → verify) ────────────────────────

    async def confirm(self, tenant_id: str, confirmation_id: str) -> dict:
        pending = await self._pending.find(confirmation_id, tenant_id)
        if not pending:
            return {"status": "not_found"}
        if pending.status not in ("action_required", "waiting_for_user"):
            return {"status": pending.status, "message": "No longer awaiting confirmation"}

        await self._pending.set_status(confirmation_id, tenant_id, "waiting_for_user")

        username = await self._resolve_username(pending.account_id, tenant_id)
        instruction = self._instruction(
            tenant_id, pending.account_id, pending.operation_id,
            pending.body, pending.media_paths, username=username,
        )

        # IMMEDIATE-PUBLISH MODEL: always open a FRESH browser and re-prepare
        # (idempotent — login persists via the on-disk profile), then submit
        # straight through. No live-session reuse: opening the browser a second
        # time for publish is expected, and publishing follows immediately. This
        # is identical to Reddit and uniform across every Hermes platform.
        adapter = self._adapter_factory(pending.session_profile_key)
        try:
            await _maybe_start(adapter)
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
        pending = await self._pending.find(confirmation_id, tenant_id)
        if not pending:
            return {"status": "not_found"}
        # Nothing to tear down: prepare does not keep a browser alive in the
        # immediate-publish model, so cancel just marks the record cancelled.
        await self._pending.set_status(confirmation_id, tenant_id, "cancelled")
        log.info("Instagram publish cancelled", op=pending.operation_id, tenant=tenant_id)
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
                    "caption": p.body,
                    "media_count": len(p.media_paths),
                },
                "expires_at": p.expires_at.isoformat() if p.expires_at else None,
            }
            for p in items
            if p.platform == C.PLATFORM
        ]


async def _maybe_start(adapter) -> None:
    start = getattr(adapter, "start", None)
    if callable(start):
        await start()


async def _maybe_close(adapter) -> None:
    close = getattr(adapter, "close", None)
    if callable(close):
        await close()
