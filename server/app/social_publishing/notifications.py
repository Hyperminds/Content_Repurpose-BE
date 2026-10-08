"""WebSocket publishing notifications — pushes real-time updates to tenants.

Integrates with the existing TrendZZo WebSocket manager to notify connected
users when their publishing jobs succeed or fail. No credentials or sensitive
data is included in notification payloads.
"""

from typing import Optional

from app.services.logger import log


async def notify_publish_success(
    tenant_id: str,
    post_id: str,
    platform: str,
    platform_post_id: Optional[str] = None,
) -> None:
    """Notify the tenant that a post was published successfully."""
    await _emit(tenant_id, "social_publish_success", {
        "post_id": post_id,
        "platform": platform,
        "platform_post_id": platform_post_id,
        "message": f"Post published to {platform}",
    })


async def notify_publish_failed(
    tenant_id: str,
    post_id: str,
    platform: str,
    reason: str,
    retryable: bool = False,
) -> None:
    """Notify the tenant that a publishing attempt failed."""
    # Sanitize reason — never include tokens or internal details
    safe_reason = reason[:200] if reason else "Unknown error"
    if "token" in safe_reason.lower() or "bearer" in safe_reason.lower():
        safe_reason = "Authentication error"

    await _emit(tenant_id, "social_publish_failed", {
        "post_id": post_id,
        "platform": platform,
        "reason": safe_reason,
        "retryable": retryable,
        "message": f"Publishing to {platform} failed: {safe_reason}",
    })


async def notify_publish_retrying(
    tenant_id: str,
    post_id: str,
    platform: str,
    attempt: int,
    max_attempts: int,
) -> None:
    """Notify the tenant that a post is being retried."""
    await _emit(tenant_id, "social_publish_retrying", {
        "post_id": post_id,
        "platform": platform,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "message": f"Retrying {platform} publish (attempt {attempt}/{max_attempts})",
    })


async def notify_account_reauth_required(
    tenant_id: str,
    account_id: str,
    platform: str,
) -> None:
    """Notify the tenant that an account needs re-authentication."""
    await _emit(tenant_id, "social_account_reauth", {
        "account_id": account_id,
        "platform": platform,
        "message": f"Your {platform} account needs to be reconnected",
    })


async def _emit(tenant_id: str, event: str, data: dict) -> None:
    """Send a WebSocket event to the tenant (user_id = tenant_id in current system)."""
    try:
        from app.ws.manager import ws_manager
        await ws_manager.send_to_user(tenant_id, event, data)
    except Exception as e:
        # WebSocket delivery is best-effort — never fail the publishing flow
        log.debug("WS notification delivery failed", event=event, error=str(e)[:100])
