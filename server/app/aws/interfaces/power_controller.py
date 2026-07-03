"""
PowerController interface (AWS boundary).

The high-level power abstraction the rest of Trendzzo depends on for the AWS
integration phase. It aggregates the three single-responsibility providers:

    startup()  → WorkspaceWakeProvider.wake()
    shutdown() → WorkspaceShutdownProvider.shutdown()
    status()   → InstanceStatusProvider.get_status()

Design (SOLID):
  • Interface Segregation — the small providers can be used independently; this
    facade is a convenience for callers that need all three.
  • Dependency Inversion — the canonical concrete implementation is *composed*
    from the three provider interfaces via dependency injection; it does not
    subclass them.
  • Open/Closed — new platforms are new provider implementations wired into the
    same facade; no caller changes.

Relationship to `app.workspace.power.PowerController`: that is the application-
level strategy used by the workspace/sleep/shutdown orchestrators. This AWS-side
interface is the *infrastructure* contract a future EC2 implementation fulfils;
an adapter bridges the two so business logic never imports the AWS module's
concrete classes (and never imports boto3).
"""

from abc import ABC, abstractmethod
from typing import Optional

from app.aws.interfaces.types import InstanceStatus, ShutdownResult, WakeResult


class PowerController(ABC):
    """Facade over wake / shutdown / status for the workspace instance."""

    @abstractmethod
    async def startup(self, instance_id: Optional[str] = None) -> WakeResult:
        """Start the instance (delegates to the wake provider). See WorkspaceWakeProvider.wake()."""
        raise NotImplementedError

    @abstractmethod
    async def shutdown(self, instance_id: Optional[str] = None) -> ShutdownResult:
        """Stop the instance (delegates to the shutdown provider). See WorkspaceShutdownProvider.shutdown()."""
        raise NotImplementedError

    @abstractmethod
    async def status(self, instance_id: Optional[str] = None) -> InstanceStatus:
        """Read instance status (delegates to the status provider). See InstanceStatusProvider.get_status()."""
        raise NotImplementedError
