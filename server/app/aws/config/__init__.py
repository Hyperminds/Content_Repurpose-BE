"""
AWS config — concrete configuration/environment providers.

Interfaces-only phase: no implementations yet. Later, this package will hold
env-backed implementations of the config interfaces, e.g.:

    OsEnvironmentProvider(EnvironmentProvider)          → reads os.environ
    EnvAWSConfigurationProvider(AWSConfigurationProvider)
        → resolves AWS_REGION / INSTANCE_ID / retry + timeout policy from an
          injected EnvironmentProvider (Dependency Inversion)

These never expose credentials; boto3 resolves those via its default provider
chain in the `services` layer. This is also the seam where config could later be
sourced from SSM Parameter Store / Secrets Manager without changing callers.

Implemented:
    OsEnvironmentProvider        — EnvironmentProvider backed by os.environ
    EnvAWSConfigurationProvider  — AWSConfigurationProvider with validation
"""

from app.aws.config.environment import OsEnvironmentProvider
from app.aws.config.aws_configuration import EnvAWSConfigurationProvider

__all__ = ["OsEnvironmentProvider", "EnvAWSConfigurationProvider"]
