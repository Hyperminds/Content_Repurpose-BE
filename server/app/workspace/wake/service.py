"""
WakeService — lightweight business logic for waking a stopped backend.

Delegates the power action to an injected PowerController obtained from the
factory (`get_power_controller`), so the strategy is chosen by configuration:
    • production → EC2PowerController (POWER_CONTROLLER=ec2)
    • local/dev  → LocalPowerController (logs only)
    • future     → Docker / Kubernetes / Railway / Render adapters
The service is entirely strategy-agnostic — new platform adapters need no change
here.

Wake behavior (by design):
    • trigger_startup() fires PowerController.startup() in the background and
      returns immediately with {"status": "starting"}. It does NOT wait for the
      instance to become Running — the frontend Health Polling Service detects
      readiness via GET /workspace/health.
"""

import asyncio
from typing import Optional

from app.services.logger import log
from app.workspace.power.base import PowerController
from app.workspace.power.factory import get_power_controller
from app.workspace.wake.status import WakeStatus


class WakeService:
    """Triggers workspace startup and reports wake status via a PowerController."""

    def __init__(self, power_controller: Optional[PowerController] = None) -> None:
        # Injected for tests; defaults to the configured strategy (EC2 in prod).
        self._power = power_controller or get_power_controller()

    def trigger_startup(self) -> dict:
        """
        Kick off startup and return immediately.

        Fire-and-forget: the actual PowerController.startup() runs as a
        background task so the HTTP response is not blocked on the (slower)
        infrastructure call. Always returns {"status": "starting"} — readiness
        is confirmed later by the frontend health poll.
        """
        try:
            asyncio.get_running_loop().create_task(self._run_startup())
        except RuntimeError:
            # No running loop (shouldn't happen inside an async route). Fall back
            # to a direct schedule so the request still returns quickly.
            asyncio.ensure_future(self._run_startup())
        return {"status": "starting"}

    async def _run_startup(self) -> None:
        """Background task: invoke the strategy's startup(), log any failure."""
        try:
            result = await self._power.startup()
            if isinstance(result, dict) and not result.get("ok", True):
                log.error(f"[wake] startup reported failure: {result}")
            else:
                log.info(f"[wake] startup triggered via '{self._power.name}' strategy")
        except Exception as e:  # pragma: no cover - defensive
            log.error(f"[wake] startup() raised: {e}")

    async def status(self) -> WakeStatus:
        """Report the current wake/power status of the workspace."""
        result = await self._power.status()
        return WakeStatus.from_power_result(result)


def get_wake_service() -> WakeService:
    """DI provider / accessor for the WakeService (uses the configured strategy)."""
    return WakeService()
