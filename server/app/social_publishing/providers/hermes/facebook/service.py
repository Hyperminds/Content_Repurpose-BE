"""Facebook user-assisted publishing service (profile + page targets).

Coordinates prepare / confirm / cancel for Facebook user-assisted posts,
mirroring Instagram/Reddit user-assisted services. Owns:
  - the browser adapter lifecycle (start/close) per request
  - the pending-confirmation persistence (same operation_id throughout)
  - audit events
  - tenant/account scoping (isolated persistent browser profile per account)
  - TARGET resolution: profile vs page is derived from the connected account
    (account_type/platform_account_id), and threaded through the instruction so
    the ONE workflow can be target-aware.

It contains NO browser code and NO Facebook page logic. Login persists across
requests via the on-disk persistent profile, so confirm re-prepares (idempotent)
then submits within ONE browser session.
"""

from typing import Optional

from app.services.logger import log
from app.social_publishing.providers.errors import (
    ExternalResultStatus,
    PublishInstruction,
)
from app.social_publishing.providers.observability import emit, PublishEvent
from app.social_publishing.providers.hermes.playwright_adapter import PlaywrightHermesAdapter
from app.social_publishing.providers.hermes.facebook import constants as C
from app.social_publishing.providers.hermes.facebook.validation import validate_post
from app.social_publishing.providers.hermes.facebook.workflow import HermesFacebookWorkflow
from app.social_publishing.providers.hermes.pending_confirmation_repository import (
    PendingConfirmationRepository,
)


def _default_adapter_factory(profile_key: str):
    return PlaywrightHermesAdapter(profile_key)


async def _default_target_resolver(account_id: str, tenant_id: str) -> dict:
    """
    Resolve the Facebook target (type + identifier + name) from the connected
    account, tenant-scoped. account_type=="page" → page target with the
    platform_account_id as the Page identifier; otherwise a profile target.
    """
    from app.social_publishing.repositories.social_accounts_repository import (
        SocialAccountsRepository,
    )
    account = await SocialAccountsRepository().find_by_id(account_id, tenant_id)
    if not account:
        return {"target_type": C.DEFAULT_TARGET_TYPE, "target_identifier": "", "target_name": ""}
    is_page = (getattr(account, "account_type", "") or "").lower() == "page"
    return {
        "target_type": C.TARGET_PAGE if is_page else C.TARGET_PROFILE,
        "target_identifier": account.platform_account_id or "",
        "target_name": account.account_name or "",
    }


class FacebookUserAssistedService:
    """Coordinates prepare / confirm / cancel for Facebook user-assisted posts."""

    def __init__(
        self,
        pending_repo: Optional[PendingConfirmationRepository] = None,
        workflow: Optional[HermesFacebookWorkflow] = None,
        adapter_factory=None,
        target_resolver=None,
    ) -> None:
        self._pending = pending_repo or PendingConfirmationRepository()
        self._workflow = workflow or HermesFacebookWorkflow()
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._target_resolver = target_resolver or _default_target_resolver

    def _profile_key(self, tenant_id: str, account_id: str) -> str:
        # Per-account, per-tenant isolated persistent browser profile, namespaced
        # with the platform so a Facebook profile never collides with Instagram/
        # Reddit for the same account id.
        return f"fb_{tenant_id}_{account_id}"

    async def _resolve_target(
        self, account_id: str, tenant_id: str,
        override_type: str = "", override_identifier: str = "",
    ) -> dict:
        try:
            resolved = await self._target_resolver(account_id, tenant_id) or {}
        except Exception:
            resolved = {}
        target_type = (override_type or resolved.get("target_type") or C.DEFAULT_TARGET_TYPE).lower()
        if target_type not in C.TARGET_TYPES:
            target_type = C.DEFAULT_TARGET_TYPE
        target_identifier = override_identifier or resolved.get("target_identifier", "") or ""
        return {
            "target_type": target_type,
            "target_identifier": target_identifier,
            "target_name": resolved.get("target_name", "") or "",
        }

    def _instruction(
        self, tenant_id: str, account_id: str, operation_id: str,
        text: str, media_paths: list[str], target: dict,
    ) -> PublishInstruction:
        return PublishInstruction(
            operation_id=operation_id,
            tenant_id=tenant_id,
            account_id=account_id,
            platform=C.PLATFORM,
            provider_name="hermes",
            text=text,
            media_urls=[],
            meta={
                "facebook_text": text,
                "facebook_media_paths": media_paths,
                "facebook_target_type": target["target_type"],
                "facebook_target_identifier": target["target_identifier"],
                "facebook_target_name": target["target_name"],
                "account_record_id": account_id,
            },
        )

    # ── Prepare (stops at ACTION_REQUIRED) ──────────────────────────────────────

    async def prepare(
        self, tenant_id: str, account_id: str, operation_id: str,
        text: str, media_paths: Optional[list[str]] = None,
        target_type: str = "", target_identifier: str = "",
    ) -> dict:
        media_paths = media_paths or []
        target = await self._resolve_target(account_id, tenant_id, target_type, target_identifier)

        parsed, errors = validate_post(text, media_paths, target["target_type"])
        if errors:
            return {"status": "failed", "errors": errors}

        emit(
            PublishEvent.STARTED, tenant_id=tenant_id, job_id=operation_id,
            operation_id=operation_id, provider="hermes", platform=C.PLATFORM,
            account_id=account_id, result="preparing",
        )

        profile_key = self._profile_key(tenant_id, account_id)
        instruction = self._instruction(
            tenant_id, account_id, operation_id, parsed.text, media_paths, target,
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
                title=target["target_type"],          # reuse title to persist target_type
                body=parsed.text, subreddit=target["target_identifier"],  # reuse subreddit for target id
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
                "message": result.error_message or "Your Facebook post is ready. Confirm publishing.",
                "confirmation_id": pending.id,
                "operation_id": operation_id,
                "preview": {
                    "text": parsed.text,
                    "media_count": len(media_paths),
                    "target_type": target["target_type"],
                    "target_name": target["target_name"],
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

        # Rebuild the target from the persisted record (title=target_type,
        # subreddit=target_identifier) with a resolver fallback for the name.
        resolved = await self._resolve_target(pending.account_id, tenant_id)
        target = {
            "target_type": (pending.title or resolved["target_type"]) or C.DEFAULT_TARGET_TYPE,
            "target_identifier": pending.subreddit or resolved["target_identifier"],
            "target_name": resolved["target_name"],
        }
        instruction = self._instruction(
            tenant_id, pending.account_id, pending.operation_id,
            pending.body, pending.media_paths, target,
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
        log.info("Facebook publish cancelled", op=pending.operation_id, tenant=tenant_id)
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
                    "text": p.body,
                    "media_count": len(p.media_paths),
                    "target_type": p.title or C.DEFAULT_TARGET_TYPE,
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
