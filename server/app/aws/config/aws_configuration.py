"""
EnvAWSConfigurationProvider — concrete AWSConfigurationProvider.

Resolves the (non-secret) AWS configuration from an injected EnvironmentProvider:

    AWS_REGION          required  — e.g. "us-east-1"
    INSTANCE_ID         required  — e.g. "i-0abc123def456789"
    AWS_MAX_ATTEMPTS    optional  — botocore retry attempts (default 5)
    AWS_RETRY_MODE      optional  — "adaptive" | "standard" | "legacy"
    AWS_CONNECT_TIMEOUT optional  — seconds (default 5)
    AWS_READ_TIMEOUT    optional  — seconds (default 15)

Credentials are NEVER read here — boto3 resolves them via its default provider
chain (IAM role / AWS CLI shared config / environment variables) in the service
layer. This provider only surfaces configuration and validates its shape.

Dependency Inversion: depends on EnvironmentProvider, not os.environ.
"""

import re
from typing import List, Optional

from app.aws.interfaces.aws_configuration_provider import AWSConfigurationProvider
from app.aws.interfaces.environment_provider import EnvironmentProvider
from app.aws.interfaces.types import RetryPolicy, TimeoutPolicy
from app.aws.exceptions import AWSConfigurationError
from app.aws.config.environment import OsEnvironmentProvider

# Shape validators (defensive — fail fast on obvious misconfiguration).
_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")
_INSTANCE_ID_RE = re.compile(r"^i-[0-9a-f]{8,17}$")

_VALID_RETRY_MODES = {"adaptive", "standard", "legacy"}


class EnvAWSConfigurationProvider(AWSConfigurationProvider):
    """Environment-backed AWS configuration with validation."""

    def __init__(self, env: Optional[EnvironmentProvider] = None) -> None:
        # Injected for testing; defaults to the os.environ-backed provider.
        self._env = env or OsEnvironmentProvider()

    def get_region(self) -> str:
        region = self._env.get("AWS_REGION")
        if not region:
            raise AWSConfigurationError("AWS_REGION is not configured")
        if not _REGION_RE.match(region):
            raise AWSConfigurationError(f"AWS_REGION '{region}' is not a valid region string")
        return region

    def get_instance_id(self) -> str:
        instance_id = self._env.get("INSTANCE_ID")
        if not instance_id:
            raise AWSConfigurationError("INSTANCE_ID is not configured")
        if not _INSTANCE_ID_RE.match(instance_id):
            raise AWSConfigurationError(f"INSTANCE_ID '{instance_id}' is not a valid EC2 instance id")
        return instance_id

    def get_retry_policy(self) -> RetryPolicy:
        mode = (self._env.get("AWS_RETRY_MODE", "adaptive") or "adaptive").lower()
        if mode not in _VALID_RETRY_MODES:
            mode = "adaptive"
        attempts = max(1, self._env.get_int("AWS_MAX_ATTEMPTS", 5))
        return RetryPolicy(max_attempts=attempts, mode=mode)

    def get_timeout_policy(self) -> TimeoutPolicy:
        return TimeoutPolicy(
            connect_seconds=float(self._env.get_int("AWS_CONNECT_TIMEOUT", 5)),
            read_seconds=float(self._env.get_int("AWS_READ_TIMEOUT", 15)),
        )

    def is_configured(self) -> bool:
        try:
            self.get_region()
            self.get_instance_id()
            return True
        except AWSConfigurationError:
            return False

    def validate(self) -> None:
        """
        Validate all required configuration at once, aggregating every problem.

        Raises:
            AWSConfigurationError: with a combined message listing all issues.
        """
        errors: List[str] = []
        for getter in (self.get_region, self.get_instance_id):
            try:
                getter()
            except AWSConfigurationError as e:
                errors.append(str(e))
        if errors:
            raise AWSConfigurationError("Invalid AWS configuration: " + "; ".join(errors))
