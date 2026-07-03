"""
Health status mapping — pure, SDK-agnostic.

Maps a provider-neutral InstanceState onto a HealthStatus that the workspace
health-polling / lifecycle layers can consume without knowing about EC2 or
boto3. Keeps the "is the workspace usable?" question separate from the raw
power state.
"""

from enum import Enum

from app.aws.interfaces.types import InstanceState


class HealthStatus(str, Enum):
    """Readiness classification derived from an instance's power state."""

    HEALTHY = "healthy"          # running and ready to serve
    STARTING = "starting"        # coming up — not ready yet
    STOPPING = "stopping"        # going down — not ready
    UNAVAILABLE = "unavailable"  # stopped — must be woken
    UNKNOWN = "unknown"

    @property
    def is_ready(self) -> bool:
        return self is HealthStatus.HEALTHY


_STATE_TO_HEALTH = {
    InstanceState.RUNNING: HealthStatus.HEALTHY,
    InstanceState.STARTING: HealthStatus.STARTING,
    InstanceState.STOPPING: HealthStatus.STOPPING,
    InstanceState.STOPPED: HealthStatus.UNAVAILABLE,
    InstanceState.UNKNOWN: HealthStatus.UNKNOWN,
}


def map_health_status(state: InstanceState) -> HealthStatus:
    """Map an InstanceState to a HealthStatus (UNKNOWN for anything unmapped)."""
    return _STATE_TO_HEALTH.get(state, HealthStatus.UNKNOWN)
