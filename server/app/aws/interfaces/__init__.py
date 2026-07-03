"""
AWS interfaces — the abstract boundary between Trendzzo and any cloud provider.

Interfaces ONLY (no boto3, no SDK calls). Concrete, SDK-backed implementations
land in `app.aws.services` in a later phase and are the sole place boto3 is ever
imported. Callers depend on these abstractions and receive implementations via
dependency injection.

Provided:
    Value types  — InstanceState, InstanceStatus, WakeResult, ShutdownResult,
                   RetryPolicy, TimeoutPolicy
    Interfaces   — PowerController, WorkspaceWakeProvider,
                   WorkspaceShutdownProvider, InstanceStatusProvider,
                   AWSConfigurationProvider, EnvironmentProvider
"""

from app.aws.interfaces.types import (
    InstanceState,
    InstanceStatus,
    WakeResult,
    ShutdownResult,
    RetryPolicy,
    TimeoutPolicy,
)
from app.aws.interfaces.power_controller import PowerController
from app.aws.interfaces.wake_provider import WorkspaceWakeProvider
from app.aws.interfaces.shutdown_provider import WorkspaceShutdownProvider
from app.aws.interfaces.instance_status_provider import InstanceStatusProvider
from app.aws.interfaces.aws_configuration_provider import AWSConfigurationProvider
from app.aws.interfaces.environment_provider import EnvironmentProvider

__all__ = [
    # value types
    "InstanceState",
    "InstanceStatus",
    "WakeResult",
    "ShutdownResult",
    "RetryPolicy",
    "TimeoutPolicy",
    # interfaces
    "PowerController",
    "WorkspaceWakeProvider",
    "WorkspaceShutdownProvider",
    "InstanceStatusProvider",
    "AWSConfigurationProvider",
    "EnvironmentProvider",
]
