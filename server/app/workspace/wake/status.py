"""
WakeStatus — value object describing the workspace wake state.

Built from any PowerController result (`sleep`/`wake`/`status` all return the
same envelope), so it is agnostic to the underlying strategy (Local / EC2 / …).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Coarse PowerState value → public label (aligned with EC2's Running/Stopped/…).
_STATE_LABEL = {
    "running": "Running",
    "sleeping": "Stopped",
    "starting": "Pending",
    "stopping": "Stopping",
    "unknown": "Unknown",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class WakeStatus:
    """Normalized snapshot of the workspace power state for the wake flow."""

    state: str = "unknown"       # PowerState value: running|sleeping|starting|stopping|unknown
    label: str = "Unknown"       # public label: Running|Stopped|Pending|Stopping|…
    provider: str = "local"      # which PowerController answered
    detail: str = ""
    ok: bool = True
    at: str = field(default_factory=_now_iso)

    @property
    def is_awake(self) -> bool:
        """True once the workspace is fully running."""
        return self.state == "running"

    @property
    def is_waking(self) -> bool:
        """True while the workspace is transitioning up (e.g. EC2 'Pending')."""
        return self.state == "starting"

    @classmethod
    def from_power_result(cls, result: dict) -> "WakeStatus":
        """Adapt a PowerController result envelope into a WakeStatus."""
        state = (result or {}).get("state", "unknown")
        # EC2 supplies a precise instance_state label; otherwise derive one.
        label = (result or {}).get("instance_state") or _STATE_LABEL.get(state, "Unknown")
        return cls(
            state=state,
            label=label,
            provider=(result or {}).get("provider", "local"),
            detail=(result or {}).get("detail", ""),
            ok=bool((result or {}).get("ok", True)),
            at=(result or {}).get("at", _now_iso()),
        )

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "label": self.label,
            "is_awake": self.is_awake,
            "is_waking": self.is_waking,
            "provider": self.provider,
            "detail": self.detail,
            "ok": self.ok,
            "at": self.at,
        }
