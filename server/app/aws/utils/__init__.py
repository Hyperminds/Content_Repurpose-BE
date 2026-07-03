"""
AWS utils — small, SDK-agnostic helpers for the AWS boundary.

Only pure, dependency-light helpers live here (no boto3). Example:
    map_instance_state — normalize a raw platform state string → InstanceState.

SDK-specific helpers (e.g. botocore Config builders, ARN construction) will be
added alongside the concrete `services` implementations in a later phase.
"""

from app.aws.utils.instance_state_mapper import map_instance_state
from app.aws.utils.health_status_mapper import HealthStatus, map_health_status

__all__ = ["map_instance_state", "HealthStatus", "map_health_status"]
