"""
WorkspaceShutdownProvider interface.

Single-responsibility abstraction for *stopping* (completely shutting down) the
workspace compute instance. A future concrete implementation will call the
platform SDK (e.g. EC2 StopInstances).

This is a full stop (not sleep/hibernate). Like wake, it is request-oriented:
`shutdown()` issues the stop and returns with the transitional state (typically
STOPPING); the instance reaching STOPPED is observed via InstanceStatusProvider.
"""

from abc import ABC, abstractmethod
from typing import Optional

from app.aws.interfaces.types import ShutdownResult


class WorkspaceShutdownProvider(ABC):
    """Stops / shuts down the workspace instance."""

    @abstractmethod
    async def shutdown(self, instance_id: Optional[str] = None) -> ShutdownResult:
        """
        Request that the instance stop.

        Args:
            instance_id: target instance; if None, the configured default is used.

        Returns:
            ShutdownResult describing the accepted request + transitional state.

        Raises:
            WorkspaceShutdownError: the stop request could not be issued.
            InvalidInstanceStateError: the instance can't be stopped from its
                current state.
            InstanceNotFoundError / AWSThrottlingError / AWSTimeoutError /
            AWSConfigurationError: as applicable.
        """
        raise NotImplementedError
