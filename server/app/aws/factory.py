"""
AWS composition root — dependency injection for the AWS integration layer.

Wires the concrete pieces together:

    OsEnvironmentProvider
        └─▶ EnvAWSConfigurationProvider
                └─▶ EC2PowerController (boto3)   [+ optional injected boto3.Session]

Callers ask the composition root for an abstraction (PowerController) and never
construct concrete AWS classes themselves. Each builder accepts optional
overrides so tests can inject fakes at any layer.

Credentials: none are passed anywhere — boto3.Session() resolves them via its
default provider chain (IAM role → AWS CLI shared config → environment
variables), so all three credential sources are supported with no code changes.
"""

from functools import lru_cache
from typing import Optional

from app.aws.interfaces.environment_provider import EnvironmentProvider
from app.aws.interfaces.aws_configuration_provider import AWSConfigurationProvider
from app.aws.interfaces.power_controller import PowerController
from app.aws.config.environment import OsEnvironmentProvider
from app.aws.config.aws_configuration import EnvAWSConfigurationProvider
from app.aws.services.ec2_power_controller import EC2PowerController


def build_environment_provider() -> EnvironmentProvider:
    """Build the environment provider (os.environ-backed)."""
    return OsEnvironmentProvider()


def build_configuration_provider(
    env: Optional[EnvironmentProvider] = None,
) -> AWSConfigurationProvider:
    """Build the AWS configuration provider, injecting the environment provider."""
    return EnvAWSConfigurationProvider(env or build_environment_provider())


def build_power_controller(
    config: Optional[AWSConfigurationProvider] = None,
    session: Optional["object"] = None,
) -> PowerController:
    """
    Build the AWS PowerController (EC2-backed).

    Args:
        config: an AWSConfigurationProvider; built from env if omitted.
        session: an optional pre-built boto3.Session (injectable for tests /
            custom credentials). If omitted, EC2PowerController creates a default
            Session lazily on first use.
    """
    return EC2PowerController(config or build_configuration_provider(), session=session)


@lru_cache(maxsize=1)
def get_aws_power_controller() -> PowerController:
    """Process-wide AWS PowerController (accessor / DI provider)."""
    return build_power_controller()
