"""Structured observability + audit events for hybrid publishing (Phase 24/25).

Emits provider-neutral, structured events with a consistent field set:
    tenant_id, job_id, operation_id, provider, platform, account_id, result

Events (external_publish_*):
    started, action_required, verifying, completed, failed, unknown

Two sinks:
  - structured application log (app.services.logger.log)
  - audit log (best-effort persistence to sp_publish_audit), respecting the
    existing audit posture

Every field is sanitized before emission. Payloads NEVER contain passwords,
cookies, tokens, refresh tokens, authorization codes, or session contents.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from app.services.logger import log

# Keys that must never appear in an observability payload, even if a caller
# accidentally passes them through.
_FORBIDDEN_KEYS = {
    "password", "cookie", "cookies", "token", "access_token", "refresh_token",
    "authorization", "authorization_code", "session", "secret", "client_secret",
    "api_key", "credentials", "encrypted_reference", "encrypted_credentials",
}


class PublishEvent(str, Enum):
    STARTED = "external_publish_started"
    ACTION_REQUIRED = "external_publish_action_required"
    VERIFYING = "external_publish_verifying"
    COMPLETED = "external_publish_completed"
    FAILED = "external_publish_failed"
    UNKNOWN = "external_publish_unknown"


def _scrub(fields: dict) -> dict:
    """Drop any forbidden keys and truncate long free-text values."""
    clean: dict = {}
    for k, v in fields.items():
        if k.lower() in _FORBIDDEN_KEYS:
            continue
        if isinstance(v, str) and len(v) > 300:
            v = v[:300]
        clean[k] = v
    return clean


def emit(
    event: PublishEvent,
    *,
    tenant_id: str,
    job_id: str,
    operation_id: str,
    provider: str,
    platform: str,
    account_id: str,
    result: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """
    Emit a structured observability event (log + best-effort audit).

    Never raises — observability must not break the publishing flow.
    """
    payload = _scrub({
        "event": event.value,
        "tenant_id": tenant_id,
        "job_id": job_id,
        "operation_id": operation_id,
        "provider": provider,
        "platform": platform,
        "account_id": account_id,
        "result": result,
        "reason": reason,
    })

    # 1) structured application log
    try:
        log.info(event.value, **payload)
    except Exception:
        pass

    # 2) best-effort audit persistence — only when an event loop is running.
    try:
        import asyncio
        loop = asyncio.get_running_loop()
        loop.create_task(_write_audit(payload))
    except RuntimeError:
        # No running loop (e.g. sync call/test) — the log above already captured it.
        pass
    except Exception:
        pass


async def _write_audit(payload: dict) -> None:
    """Persist an audit record. Best-effort; never blocks publishing."""
    try:
        from app.database import db
        record = dict(payload)
        record["created_at"] = datetime.now(timezone.utc)
        await db["sp_publish_audit"].insert_one(record)
    except Exception as e:
        log.debug("Audit write failed", error=str(e)[:100])
