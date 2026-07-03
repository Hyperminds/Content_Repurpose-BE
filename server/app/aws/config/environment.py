"""
OsEnvironmentProvider — concrete EnvironmentProvider backed by os.environ.

This is the ONLY place in the AWS module that reads process environment
variables. Everything else depends on the EnvironmentProvider abstraction, so
the source can later be swapped (SSM Parameter Store / Secrets Manager) without
touching callers.
"""

import os
from typing import Optional

from app.aws.interfaces.environment_provider import EnvironmentProvider
from app.aws.exceptions import EnvironmentVariableMissingError

_TRUTHY = {"1", "true", "yes", "on"}


class OsEnvironmentProvider(EnvironmentProvider):
    """Reads configuration values from the process environment."""

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        value = os.environ.get(key)
        return value if value not in (None, "") else default

    def require(self, key: str) -> str:
        value = os.environ.get(key)
        if value in (None, ""):
            raise EnvironmentVariableMissingError(key)
        return value

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = os.environ.get(key)
        if value in (None, ""):
            return default
        return value.strip().lower() in _TRUTHY

    def get_int(self, key: str, default: int) -> int:
        value = os.environ.get(key)
        if value in (None, ""):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def is_lambda(self) -> bool:
        # The Lambda runtime always sets AWS_LAMBDA_FUNCTION_NAME.
        return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
