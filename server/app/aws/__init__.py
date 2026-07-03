"""
AWS integration module — the single boundary between Trendzzo and AWS.

Purpose
-------
Prepare Trendzzo for AWS integration WITHOUT leaking infrastructure-specific code
into the business logic. Everything AWS-related is (and will be) confined to this
package. The rest of Trendzzo depends only on the abstract interfaces here and
receives concrete implementations via dependency injection.

    ┌─────────────────────────────────────────────────────────────┐
    │ app.aws                                                       │
    │   interfaces/  ← abstract contracts (this phase) — NO SDK      │
    │   exceptions/  ← provider-neutral error hierarchy             │
    │   config/      ← env/config providers          (future impl)  │
    │   services/    ← SDK-backed providers  (ONLY place boto3 lives)│
    │   controllers/ ← app-facing adapters            (future impl)  │
    │   utils/       ← pure, SDK-agnostic helpers                   │
    └─────────────────────────────────────────────────────────────┘

Rules
-----
1. The rest of Trendzzo MUST NEVER import boto3 directly — only `app.aws.services`
   (future phase) may.
2. Callers depend on `app.aws.interfaces` and catch `app.aws.exceptions` — never
   boto3's ClientError/BotoCoreError.
3. SOLID: small segregated provider interfaces; a PowerController facade composed
   from them via DI; new platforms are new implementations, not caller changes.

Phase status: INTERFACES ONLY. No AWS SDK calls are implemented.
"""

from app.aws.interfaces import (
    InstanceState,
    InstanceStatus,
    WakeResult,
    ShutdownResult,
    RetryPolicy,
    TimeoutPolicy,
    PowerController,
    WorkspaceWakeProvider,
    WorkspaceShutdownProvider,
    InstanceStatusProvider,
    AWSConfigurationProvider,
    EnvironmentProvider,
)
from app.aws import exceptions
from app.aws.factory import (
    build_environment_provider,
    build_configuration_provider,
    build_power_controller,
    get_aws_power_controller,
)

__all__ = [
    "InstanceState",
    "InstanceStatus",
    "WakeResult",
    "ShutdownResult",
    "RetryPolicy",
    "TimeoutPolicy",
    "PowerController",
    "WorkspaceWakeProvider",
    "WorkspaceShutdownProvider",
    "InstanceStatusProvider",
    "AWSConfigurationProvider",
    "EnvironmentProvider",
    "exceptions",
    # composition root (DI)
    "build_environment_provider",
    "build_configuration_provider",
    "build_power_controller",
    "get_aws_power_controller",
]
