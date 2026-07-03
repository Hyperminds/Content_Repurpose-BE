"""
AWS services — concrete, SDK-backed implementations of the AWS interfaces.

*** This is the ONLY package in Trendzzo permitted to import boto3. ***

Nothing is implemented yet (this phase is interfaces-only). In a later phase,
implementations such as:

    Ec2WakeProvider(WorkspaceWakeProvider)          → ec2.start_instances
    Ec2ShutdownProvider(WorkspaceShutdownProvider)  → ec2.stop_instances
    Ec2InstanceStatusProvider(InstanceStatusProvider) → ec2.describe_instances
    Ec2PowerController(PowerController)              → composes the three above

will live here, lazily importing boto3 inside methods, honouring the
RetryPolicy/TimeoutPolicy from AWSConfigurationProvider, resolving credentials
via the default provider chain (never hardcoded), and translating SDK errors
into the `app.aws.exceptions` hierarchy so callers stay SDK-agnostic.

Implemented:
    EC2PowerController — boto3-backed PowerController for a single EC2 instance.
"""

from app.aws.services.ec2_power_controller import EC2PowerController

__all__ = ["EC2PowerController"]
