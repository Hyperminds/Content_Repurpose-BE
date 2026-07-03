"""
AWSConfigurationProvider interface.

Supplies the resolved AWS configuration (region, target instance id, retry and
timeout policies) to AWS service implementations. It NEVER exposes credentials —
concrete implementations rely on the default credential chain (env / shared
config / IAM role), and this interface only surfaces non-secret configuration.

Injected into the wake/shutdown/status providers so they don't read config
themselves (Single Responsibility + Dependency Inversion).
"""

from abc import ABC, abstractmethod

from app.aws.interfaces.types import RetryPolicy, TimeoutPolicy


class AWSConfigurationProvider(ABC):
    """Resolved, non-secret AWS configuration."""

    @abstractmethod
    def get_region(self) -> str:
        """
        Return the AWS region (e.g. "us-east-1").

        Raises:
            AWSConfigurationError: if no region is configured.
        """
        raise NotImplementedError

    @abstractmethod
    def get_instance_id(self) -> str:
        """
        Return the target instance id (e.g. "i-0abc123…").

        Raises:
            AWSConfigurationError: if no instance id is configured.
        """
        raise NotImplementedError

    @abstractmethod
    def get_retry_policy(self) -> RetryPolicy:
        """Return the retry policy for SDK calls (max attempts + mode)."""
        raise NotImplementedError

    @abstractmethod
    def get_timeout_policy(self) -> TimeoutPolicy:
        """Return connect/read timeouts for SDK calls."""
        raise NotImplementedError

    @abstractmethod
    def is_configured(self) -> bool:
        """
        True when the minimum required configuration (region + instance id) is
        present. Lets callers degrade gracefully instead of raising.
        """
        raise NotImplementedError
