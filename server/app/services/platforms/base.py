"""
Shared HTTP client factory for platform publishers.

Every platform publisher should use `create_platform_client()` instead of
constructing its own httpx.AsyncClient. This guarantees:
  - Consistent timeouts (connect + read + write)
  - Proper User-Agent for API compliance
  - Mock-mode bypass check
  - Structured logging on failure

Usage:
    from app.services.platforms.base import create_platform_client, should_use_mock

    if should_use_mock():
        return get_mock_publish_result(...)

    async with create_platform_client() as client:
        response = await client.post(url, json=payload, headers=headers)
"""

from typing import Optional

import httpx

from app.config import APP_NAME, APP_VERSION, get_use_mock
from app.services.logger import log


# ── Timeout configuration (seconds) ──────────────────────────────────────────
# These are deliberately generous for media uploads but strict enough to fail
# fast on a dead endpoint.
DEFAULT_CONNECT_TIMEOUT: float = 10.0
DEFAULT_READ_TIMEOUT: float = 30.0
DEFAULT_WRITE_TIMEOUT: float = 60.0   # media uploads can be large
DEFAULT_POOL_TIMEOUT: float = 10.0

_DEFAULT_TIMEOUT = httpx.Timeout(
    connect=DEFAULT_CONNECT_TIMEOUT,
    read=DEFAULT_READ_TIMEOUT,
    write=DEFAULT_WRITE_TIMEOUT,
    pool=DEFAULT_POOL_TIMEOUT,
)

# ── User-Agent ────────────────────────────────────────────────────────────────
_USER_AGENT = f"{APP_NAME}/{APP_VERSION} (PublishingEngine)"


def create_platform_client(
    *,
    timeout: Optional[httpx.Timeout] = None,
    follow_redirects: bool = True,
    max_redirects: int = 5,
) -> httpx.AsyncClient:
    """
    Create a configured httpx.AsyncClient for platform API calls.

    Returns a context-manager client — use with `async with`:

        async with create_platform_client() as client:
            resp = await client.post(...)

    The caller is responsible for closing the client (the `async with` handles
    this automatically).
    """
    return httpx.AsyncClient(
        timeout=timeout or _DEFAULT_TIMEOUT,
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
        headers={"User-Agent": _USER_AGENT},
    )


def should_use_mock() -> bool:
    """
    Check if the system is in mock mode.

    Publishers should call this at the top of their publish method and return
    mock data immediately if True — no real API calls in mock mode.
    """
    return get_use_mock()


def log_platform_error(platform: str, operation: str, error: Exception) -> None:
    """Log a platform API error with structured context."""
    log.error(
        f"Platform API error: {platform}/{operation}",
        platform=platform,
        operation=operation,
        error_type=type(error).__name__,
        error_msg=str(error),
    )


def log_platform_success(platform: str, operation: str, post_id: str = "") -> None:
    """Log a successful platform API operation."""
    log.info(
        f"Platform API success: {platform}/{operation}",
        platform=platform,
        operation=operation,
        platform_post_id=post_id,
    )
