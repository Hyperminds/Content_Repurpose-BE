"""
LocalPowerController — the default, infrastructure-free strategy.

Only LOGS actions and tracks an in-memory power state. Used for local
development and for exercising the startup/shutdown orchestration end-to-end
without touching any real runtime. It is the safe default returned by the
factory when no platform strategy is configured.
"""

from app.services.logger import log
from app.workspace.power.base import PowerController, PowerState


class LocalPowerController(PowerController):
    """No-op strategy: logs the action and flips an in-memory state flag."""

    name = "local"

    def __init__(self) -> None:
        self._state = PowerState.RUNNING

    async def startup(self) -> dict:
        self._state = PowerState.RUNNING
        log.info("[power:local] startup() — no infrastructure action (logged only)")
        return self._result(
            "startup",
            state=self._state,
            detail="Local strategy: logged startup; no runtime started.",
        )

    async def shutdown(self) -> dict:
        self._state = PowerState.SLEEPING
        log.info("[power:local] shutdown() — no infrastructure action (logged only)")
        return self._result(
            "shutdown",
            state=self._state,
            detail="Local strategy: logged shutdown; no runtime stopped.",
        )

    async def status(self) -> dict:
        log.info(f"[power:local] status() — {self._state.value}")
        return self._result(
            "status",
            state=self._state,
            detail="Local strategy: in-memory state only.",
        )
