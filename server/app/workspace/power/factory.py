"""
PowerControllerFactory — strategy selection + dependency injection.

Central registry mapping a strategy key ("local", "ec2", …) to its
PowerController class. The concrete strategy is chosen from configuration
(POWER_CONTROLLER env var), so callers stay decoupled from the choice.

Register a new strategy by adding it to _REGISTRY — nothing else changes.
"""

from functools import lru_cache
from typing import Dict, Type

from app.services.logger import log
from app.workspace.power.base import PowerController
from app.workspace.power.local import LocalPowerController
from app.workspace.power.ec2 import EC2PowerController
from app.workspace.power.providers import (
    DockerPowerController,
    KubernetesPowerController,
    AzurePowerController,
    RenderPowerController,
    RailwayPowerController,
)

# Strategy registry: key → controller class.
_REGISTRY: Dict[str, Type[PowerController]] = {
    "local": LocalPowerController,
    "ec2": EC2PowerController,
    "docker": DockerPowerController,
    "kubernetes": KubernetesPowerController,
    "azure": AzurePowerController,
    "render": RenderPowerController,
    "railway": RailwayPowerController,
}

DEFAULT_STRATEGY = "local"


class PowerControllerFactory:
    """Builds PowerController instances by strategy key."""

    @staticmethod
    def available() -> list[str]:
        """List registered strategy keys."""
        return sorted(_REGISTRY.keys())

    @staticmethod
    def register(key: str, controller_cls: Type[PowerController]) -> None:
        """Register (or override) a strategy at runtime."""
        _REGISTRY[key.lower()] = controller_cls

    @staticmethod
    def create(strategy: str) -> PowerController:
        """
        Instantiate the controller for `strategy`. Falls back to the local
        (log-only) strategy if the key is unknown OR if the selected strategy
        fails to construct (e.g. "ec2" selected but AWS_REGION/INSTANCE_ID are
        missing). The fallback is logged loudly so a misconfiguration is visible
        without crashing app startup or accidentally acting on infrastructure.
        """
        key = (strategy or "").lower()
        controller_cls = _REGISTRY.get(key)
        if controller_cls is None:
            log.warning(f"[power] unknown strategy '{strategy}', falling back to 'local'")
            return LocalPowerController()
        try:
            return controller_cls()
        except Exception as e:
            log.error(f"[power] strategy '{key}' failed to initialize ({e}); falling back to 'local'")
            return LocalPowerController()


@lru_cache(maxsize=1)
def _controller_singleton() -> PowerController:
    """Create the configured PowerController once (process-wide)."""
    from app.config import POWER_CONTROLLER
    return PowerControllerFactory.create(POWER_CONTROLLER)


def get_power_controller() -> PowerController:
    """
    Dependency-injection provider / accessor for the shared PowerController.

    Used by the SleepOrchestrator and any FastAPI route via
    `Depends(get_power_controller)`.
    """
    return _controller_singleton()
