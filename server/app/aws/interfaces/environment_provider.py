"""
EnvironmentProvider interface.

Abstraction over the runtime environment (environment variables + runtime
detection). Injected into configuration/providers so they never read `os.environ`
directly — this keeps them testable (inject a fake) and is the seam where, e.g.,
AWS Secrets Manager or SSM Parameter Store could later be sourced without
touching callers.

Dependency Inversion: high-level config depends on THIS abstraction, not on the
concrete environment.
"""

from abc import ABC, abstractmethod
from typing import Optional


class EnvironmentProvider(ABC):
    """Read-only access to runtime configuration values."""

    @abstractmethod
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Return the value for `key`, or `default` if unset."""
        raise NotImplementedError

    @abstractmethod
    def require(self, key: str) -> str:
        """
        Return the value for `key`.

        Raises:
            EnvironmentVariableMissingError: if the variable is unset/empty.
        """
        raise NotImplementedError

    @abstractmethod
    def get_bool(self, key: str, default: bool = False) -> bool:
        """Return a boolean-parsed env value ("true"/"1"/"yes" → True)."""
        raise NotImplementedError

    @abstractmethod
    def get_int(self, key: str, default: int) -> int:
        """Return an int-parsed env value, falling back to `default`."""
        raise NotImplementedError

    @abstractmethod
    def is_lambda(self) -> bool:
        """True when running inside the AWS Lambda runtime."""
        raise NotImplementedError
