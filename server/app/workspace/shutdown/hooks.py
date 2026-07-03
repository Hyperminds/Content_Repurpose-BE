"""
ShutdownHooks — the concrete steps of the complete-shutdown sequence.

Reuses SleepHooks for the steps shared with the sleep flow (close WebSockets,
stop scheduler, flush metrics, save activity, persist state) and adds the
shutdown-specific steps that operate on the ShutdownGate (stop accepting HTTP,
drain in-flight, reject AI/publishing) plus flushing logs.

Every hook is async, single-responsibility, and fail-safe (returns a result
dict, never raises). Business logic is untouched — hooks flip gate flags and
call existing shutdown/flush entry points only.
"""

import logging
from typing import Optional

from app.workspace.sleep.hooks import SleepHooks
from app.workspace.shutdown.state import shutdown_gate


class ShutdownHooks:
    def __init__(self, sleep_hooks: Optional[SleepHooks] = None, drain_timeout_seconds: int = 30) -> None:
        self._sleep = sleep_hooks or SleepHooks()
        self._drain_timeout = drain_timeout_seconds

    # ── shutdown-specific steps ──────────────────────────────────────────────
    async def stop_accepting_requests(self) -> dict:
        """Close the HTTP gate — the middleware now 503s new requests."""
        shutdown_gate.close_http()
        return {"ok": True, "accepting_http": False}

    async def drain_requests(self) -> dict:
        """Wait for in-flight requests to finish (bounded by the drain timeout)."""
        drained = await shutdown_gate.drain(self._drain_timeout)
        return {"ok": True, "drained": drained, "in_flight": shutdown_gate.in_flight}

    async def reject_ai_jobs(self) -> dict:
        """Flip the gate so new AI-generation jobs are refused."""
        shutdown_gate.reject_ai()
        return {"ok": True, "accepting_ai": False}

    async def reject_publishing_jobs(self) -> dict:
        """Flip the gate so new publishing jobs are refused."""
        shutdown_gate.reject_publishing()
        return {"ok": True, "accepting_publishing": False}

    async def flush_logs(self) -> dict:
        """Flush all logging handlers so nothing is lost on process exit."""
        try:
            flushed = 0
            for logger_name in (None, "trendzzo"):
                lg = logging.getLogger(logger_name)
                for handler in list(lg.handlers):
                    try:
                        handler.flush()
                        flushed += 1
                    except Exception:
                        pass
            logging.shutdown  # reference kept intentionally; do not call (closes handlers)
            return {"ok": True, "handlers_flushed": flushed}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── reused (delegated) steps ─────────────────────────────────────────────
    async def close_websockets(self) -> dict:
        return await self._sleep.close_websockets()

    async def stop_scheduler(self) -> dict:
        return await self._sleep.stop_scheduler()

    async def flush_metrics(self) -> dict:
        return await self._sleep.flush_metrics()

    async def save_activity(self) -> dict:
        return await self._sleep.save_activity()

    async def persist_state(self) -> dict:
        # A complete shutdown persists the workspace as "stopped".
        return await self._sleep.persist_state("stopped")
