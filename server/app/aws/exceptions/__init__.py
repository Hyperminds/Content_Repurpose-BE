"""
AWS boundary exceptions.

A provider-neutral exception hierarchy raised by AWS interface implementations.
Callers in the rest of Trendzzo catch these abstractions — never boto3's
`ClientError`/`BotoCoreError` — so business logic stays decoupled from the SDK.

Hierarchy:
    AWSError
    ├─ AWSConfigurationError
    │    └─ EnvironmentVariableMissingError
    ├─ InstanceNotFoundError
    ├─ InvalidInstanceStateError
    ├─ AWSThrottlingError
    ├─ AWSTimeoutError
    ├─ WorkspaceWakeError
    └─ WorkspaceShutdownError
"""


class AWSError(Exception):
    """Base class for every error crossing the AWS boundary."""


class AWSConfigurationError(AWSError):
    """AWS configuration is missing or invalid (region, instance id, etc.)."""


class EnvironmentVariableMissingError(AWSConfigurationError):
    """A required environment variable was not set."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Required environment variable '{name}' is not set")


class InstanceNotFoundError(AWSError):
    """The target instance id does not exist / is not visible."""


class InvalidInstanceStateError(AWSError):
    """The instance is in a state that does not allow the requested action."""


class AWSThrottlingError(AWSError):
    """The provider throttled the request (retries exhausted)."""


class AWSTimeoutError(AWSError):
    """The provider call timed out."""


class AWSAccessDeniedError(AWSError):
    """The caller is not authorized to perform the action (IAM/AccessDenied)."""


class WorkspaceWakeError(AWSError):
    """A wake/startup request failed."""


class WorkspaceShutdownError(AWSError):
    """A shutdown/stop request failed."""


__all__ = [
    "AWSError",
    "AWSConfigurationError",
    "EnvironmentVariableMissingError",
    "InstanceNotFoundError",
    "InvalidInstanceStateError",
    "AWSThrottlingError",
    "AWSTimeoutError",
    "AWSAccessDeniedError",
    "WorkspaceWakeError",
    "WorkspaceShutdownError",
]
