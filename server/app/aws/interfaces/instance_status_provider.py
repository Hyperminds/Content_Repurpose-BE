"""
InstanceStatusProvider interface.

Single-responsibility abstraction for reading the current state of the workspace
compute instance. A future concrete implementation will call the platform SDK
(e.g. EC2 DescribeInstances) and map the raw state onto InstanceState.

Interface Segregation: status reads are separated from wake/shutdown mutations so
a caller that only needs to observe state does not depend on mutating methods.
"""

from abc import ABC, abstractmethod
from typing import Optional

from app.aws.interfaces.types import InstanceStatus


class InstanceStatusProvider(ABC):
    """Reads the power/lifecycle state of the workspace instance."""

    @abstractmethod
    async def get_status(self, instance_id: Optional[str] = None) -> InstanceStatus:
        """
        Return the current status of the instance.

        Args:
            instance_id: target instance; if None, the configured default is used.

        Returns:
            InstanceStatus with a provider-neutral InstanceState.

        Raises:
            InstanceNotFoundError: the instance id is unknown.
            AWSThrottlingError / AWSTimeoutError: transient provider failures.
            AWSConfigurationError: missing/invalid configuration.
        """
        raise NotImplementedError
