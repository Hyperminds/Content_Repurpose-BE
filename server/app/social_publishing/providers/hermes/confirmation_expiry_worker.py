"""Confirmation expiry worker (Phase 12).

Periodically expires user-assisted pending confirmations that were never
confirmed within the bounded window. An EXPIRED confirmation simply will not
publish — there is NO automatic retry and NO automatic publish.

Runs as a background asyncio task alongside the other social-publishing workers.
Only started when the Hermes provider is enabled.
"""

import asyncio
import os
from typing import Optional

from app.services.logger import log
from app.social_publishing.providers.hermes.pending_confirmation_repository import (
    PendingConfirmationRepository,
)


def _poll_interval() -> int:
    try:
        return int(os.getenv("HERMES_CONFIRMATION_SWEEP_SECONDS", "60"))
    except (TypeError, ValueError):
        return 60


class ConfirmationExpiryWorker:
    """Background sweeper that marks overdue confirmations EXPIRED."""

    def __init__(
        self,
        pending_repo: Optional[PendingConfirmationRepository] = None,
        poll_interval: Optional[int] = None,
    ) -> None:
        self._pending = pending_repo or PendingConfirmationRepository()
        self._poll_interval = poll_interval or _poll_interval()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("ConfirmationExpiryWorker started", interval_s=self._poll_interval)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        log.info("ConfirmationExpiryWorker stopped")

    @property
    def running(self) -> bool:
        return self._running

    async def _loop(self) -> None:
        while self._running:
            try:
                expired = await self._pending.expire_due()
                if expired:
                    log.info("Expired pending confirmations", count=expired)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("ConfirmationExpiryWorker error", error=str(e)[:120])
            # Also close any kept-alive browser sessions (prepare → confirm
            # reuse optimization) that are past their TTL / idle timeout, so a
            # prepared Chromium never leaks when the user never confirms.
            try:
                from app.social_publishing.providers.hermes.live_session_cache import (
                    live_session_cache,
                )
                await live_session_cache.sweep()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("Live-session sweep error", error=str(e)[:120])
            await asyncio.sleep(self._poll_interval)
