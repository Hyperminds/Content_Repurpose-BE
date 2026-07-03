"""
ShutdownWatcher — the periodic driver for Automatic Backend Shutdown.

Every configurable interval it evaluates the ShutdownDecisionEngine; when
shutdown is allowed it runs the ShutdownOrchestrator once and stops looping.

Lifecycle-managed (start/stop) from the app lifespan. DISABLED by default via
config (AUTO_SHUTDOWN_ENABLED) — it is only meaningful in a deployment where an
external always-on service can wake the instance back up afterwards, so it must
be turned on explicitly.
"""

import asyncio
from typing import Optional

from app.services.logger import log
from app.workspace.shutdown.engine import ShutdownDecisionEngine, get_shutdown_engine
from app.workspace.shutdown.orchestrator import ShutdownOrchestrator


class ShutdownWatcher:
    def __init__(
        self,
        interval_seconds: int = 60,
        engine: Optional[ShutdownDecisionEngine] = None,
        orchestrator: Optional[ShutdownOrchestrator] = None,
    ) -> None:
        self.interval_seconds = max(5, interval_seconds)
        self._engine = engine or get_shutdown_engine()
        self._orchestrator = orchestrator or ShutdownOrchestrator(engine=self._engine)
        self._task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info(f"✓ Shutdown watcher started (interval={self.interval_seconds}s)")

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_seconds)
                if not self._running:
                    break
                decision = await self._engine.evaluate()
                if decision.allowed:
                    log.info(f"[shutdown-watcher] shutdown allowed — {decision.reason}")
                    self._running = False  # stop looping; this is a one-shot action
                    await self._orchestrator.shutdown(decision)
                    break
                # else: still active — keep watching quietly.
            except asyncio.CancelledError:
                break
            except Exception as e:  # pragma: no cover - defensive
                log.error(f"[shutdown-watcher] loop error: {e}")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None


_watcher: Optional[ShutdownWatcher] = None


def get_shutdown_watcher() -> ShutdownWatcher:
    """Return the process-wide watcher, built from config on first use."""
    global _watcher
    if _watcher is None:
        from app.config import AUTO_SHUTDOWN_INTERVAL_SECONDS
        _watcher = ShutdownWatcher(interval_seconds=AUTO_SHUTDOWN_INTERVAL_SECONDS)
    return _watcher
