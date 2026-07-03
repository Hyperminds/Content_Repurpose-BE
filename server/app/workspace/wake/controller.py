"""
WakeController — thin request-layer coordinator for the wake endpoints.

Sits between the FastAPI routes and the WakeService: it keeps the routes free of
business logic and returns plain dicts ready for the response models. Kept
separate from the service so the transport concern (HTTP) and the domain concern
(power control) stay decoupled.
"""

from typing import Optional

from app.workspace.wake.service import WakeService, get_wake_service


class WakeController:
    """Coordinates wake requests for the API layer."""

    def __init__(self, service: Optional[WakeService] = None) -> None:
        self._service = service or get_wake_service()

    async def wake(self) -> dict:
        """Handle POST /workspace/wake — trigger startup, return {"status":"starting"}."""
        return self._service.trigger_startup()

    async def status(self) -> dict:
        """Handle GET /workspace/wake/status — return the current wake status."""
        status = await self._service.status()
        return status.to_dict()


def get_wake_controller() -> WakeController:
    """FastAPI dependency provider for the WakeController."""
    return WakeController()
