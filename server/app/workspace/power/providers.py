"""
Future PowerController strategies — ARCHITECTURE ONLY.

Each class conforms to the PowerController contract (startup/shutdown/status)
but performs no infrastructure work. They import NO SDKs and raise
NotImplementedError for the mutating actions, so an unconfigured strategy fails
loudly instead of silently doing nothing.

When a strategy is implemented, replace the bodies with the real calls (kept
behind this interface) and it's already registered in factory.py. The rest of
Trendzzo — orchestrators, wake service, endpoints, DI — does not change, and
never imports a platform SDK directly.

Intended implementations:
  • DockerPowerController     → start/stop a container (Docker Engine API)
  • KubernetesPowerController → scale a Deployment 0↔N replicas (k8s client)
  • AzurePowerController      → start/deallocate a VM (Azure SDK)
  • RenderPowerController     → resume/suspend a service (Render API)
  • RailwayPowerController    → resume/pause a service (Railway API)

Note: EC2PowerController is fully implemented in `ec2.py` (boto3), not here.
"""

from app.workspace.power.base import PowerController, PowerState


class _PlannedStrategy(PowerController):
    """
    Base for not-yet-implemented strategies.

    startup()/shutdown() raise NotImplementedError so an unconfigured platform
    can never silently pretend to act on infrastructure. status() is read-only
    and safe: it reports the strategy as a stub.
    """

    #: One-line description of what the real implementation will do.
    plan: str = ""

    async def startup(self) -> dict:
        raise NotImplementedError(f"{self.name} startup() not implemented — {self.plan}")

    async def shutdown(self) -> dict:
        raise NotImplementedError(f"{self.name} shutdown() not implemented — {self.plan}")

    async def status(self) -> dict:
        return self._result(
            "status",
            ok=False,
            implemented=False,
            state=PowerState.UNKNOWN,
            detail=f"{self.name} strategy is an architecture stub — {self.plan}",
        )


class DockerPowerController(_PlannedStrategy):
    """Start/stop a Docker container via the Docker Engine API."""

    name = "docker"
    plan = "start/stop a Docker container via the Docker Engine API"


class KubernetesPowerController(_PlannedStrategy):
    """Scale a Deployment between 0 and N replicas via the Kubernetes client."""

    name = "kubernetes"
    plan = "scale a Deployment 0<->N replicas via the Kubernetes API"


class AzurePowerController(_PlannedStrategy):
    """Start/deallocate an Azure VM via the Azure SDK."""

    name = "azure"
    plan = "start/deallocate an Azure VM via the Azure compute SDK"


class RenderPowerController(_PlannedStrategy):
    """Resume/suspend a Render service via the Render REST API."""

    name = "render"
    plan = "resume/suspend a Render service via the Render API"


class RailwayPowerController(_PlannedStrategy):
    """Resume/pause a Railway service via the Railway API."""

    name = "railway"
    plan = "resume/pause a Railway service via the Railway API"
