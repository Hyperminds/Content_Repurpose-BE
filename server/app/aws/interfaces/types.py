"""
Shared value types for the AWS integration boundary.

Pure, SDK-agnostic data structures used across the AWS interfaces. They contain
NO boto3 references — they describe the *shape* of data crossing the boundary so
the rest of Trendzzo can depend on stable, provider-neutral types.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class InstanceState(str, Enum):
    """
    Provider-neutral lifecycle state of a compute instance.

    Concrete providers (e.g. the EC2 implementation) map their raw platform
    states onto these values so callers never depend on platform-specific
    vocabulary. EC2 mapping: pending→STARTING, running→RUNNING,
    stopping/shutting-down→STOPPING, stopped→STOPPED, terminated→UNKNOWN.
    """

    STARTING = "starting"        # coming up (EC2: pending)
    RUNNING = "running"          # up and serving
    STOPPING = "stopping"        # going down (EC2: stopping / shutting-down)
    STOPPED = "stopped"          # fully halted
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """Human-facing label (Running | Stopped | Stopping | Starting | Unknown)."""
        return {
            InstanceState.STARTING: "Starting",
            InstanceState.RUNNING: "Running",
            InstanceState.STOPPING: "Stopping",
            InstanceState.STOPPED: "Stopped",
            InstanceState.UNKNOWN: "Unknown",
        }[self]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class InstanceStatus:
    """Result of an instance status query (see InstanceStatusProvider)."""

    instance_id: str
    state: InstanceState
    raw_state: str = ""            # the provider's original state string
    detail: str = ""
    checked_at: str = field(default_factory=_utc_now_iso)

    @property
    def is_running(self) -> bool:
        return self.state == InstanceState.RUNNING

    @property
    def is_stopped(self) -> bool:
        return self.state == InstanceState.STOPPED

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "state": self.state.value,
            "label": self.state.label,
            "raw_state": self.raw_state,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


@dataclass(frozen=True)
class WakeResult:
    """Result of a wake/startup request (see WorkspaceWakeProvider)."""

    instance_id: str
    requested: bool
    state: InstanceState = InstanceState.UNKNOWN
    detail: str = ""
    at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "requested": self.requested,
            "state": self.state.value,
            "label": self.state.label,
            "detail": self.detail,
            "at": self.at,
        }


@dataclass(frozen=True)
class ShutdownResult:
    """Result of a shutdown/stop request (see WorkspaceShutdownProvider)."""

    instance_id: str
    requested: bool
    state: InstanceState = InstanceState.UNKNOWN
    detail: str = ""
    at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "requested": self.requested,
            "state": self.state.value,
            "label": self.state.label,
            "detail": self.detail,
            "at": self.at,
        }


@dataclass(frozen=True)
class RetryPolicy:
    """
    SDK-agnostic retry configuration surfaced by AWSConfigurationProvider.

    A future concrete provider translates this into the underlying SDK's retry
    config (e.g. botocore's adaptive mode) — this type itself has no SDK ties.
    """

    max_attempts: int = 5
    mode: str = "adaptive"   # "adaptive" | "standard" | "legacy"


@dataclass(frozen=True)
class TimeoutPolicy:
    """SDK-agnostic connect/read timeouts (seconds)."""

    connect_seconds: float = 5.0
    read_seconds: float = 15.0
