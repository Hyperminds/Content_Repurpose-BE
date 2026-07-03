"""
WorkspaceWakeProvider interface.

Single-responsibility abstraction for *starting* (waking) the workspace compute
instance. A future concrete implementation will call the platform SDK
(e.g. EC2 StartInstances).

Fire-and-forget by contract: `wake()` requests the start and returns promptly
with the initial state (typically PENDING). It does NOT block until RUNNING —
readiness is confirmed separately via InstanceStatusProvider / health polling.
"""

from abc import ABC, abstractmethod
from typing import Optional

from app.aws.interfaces.types import WakeResult


class WorkspaceWakeProvider(ABC):
    """Starts / wakes the workspace instance."""

    @abstractmethod
    async def wake(self, instance_id: Optional[str] = None) -> WakeResult:
        """
        Request that the instance start.

        Args:
            instance_id: target instance; if None, the configured default is used.

        Returns:
            WakeResult describing the accepted request + initial state.

        Raises:
            WorkspaceWakeError: the start request could not be issued.
            InvalidInstanceStateError: the instance can't be started from its
                current state.
            InstanceNotFoundError / AWSThrottlingError / AWSTimeoutError /
            AWSConfigurationError: as applicable.
        """
        raise NotImplementedError
