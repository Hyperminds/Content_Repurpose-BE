"""
Workspace Power package.

A Strategy-Pattern architecture for controlling workspace power
(startup / shutdown / status) across different runtimes, with the concrete
strategy chosen via configuration + dependency injection.

    PowerController          — the strategy interface (startup/shutdown/status)
    LocalPowerController     — default, logs only (no infrastructure)
    EC2PowerController       — implemented (boto3): start/stop an EC2 instance
    Docker/Kubernetes/Azure/Render/Railway — architecture stubs (not implemented)
    PowerControllerFactory   — strategy registry + selection
    get_power_controller()   — DI provider for the configured strategy

SDK boundary: the rest of Trendzzo never imports a platform SDK — it talks only
to PowerController. Only concrete strategies (e.g. ec2.py) touch an SDK, lazily.
"""

from app.workspace.power.base import PowerController, PowerState
from app.workspace.power.local import LocalPowerController
from app.workspace.power.ec2 import EC2PowerController
from app.workspace.power.providers import (
    DockerPowerController,
    KubernetesPowerController,
    AzurePowerController,
    RenderPowerController,
    RailwayPowerController,
)
from app.workspace.power.factory import (
    PowerControllerFactory,
    get_power_controller,
    DEFAULT_STRATEGY,
)

__all__ = [
    "PowerController",
    "PowerState",
    "LocalPowerController",
    "EC2PowerController",
    "DockerPowerController",
    "KubernetesPowerController",
    "AzurePowerController",
    "RenderPowerController",
    "RailwayPowerController",
    "PowerControllerFactory",
    "get_power_controller",
    "DEFAULT_STRATEGY",
]
