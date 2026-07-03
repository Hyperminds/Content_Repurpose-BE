"""
Instance-state mapper — pure, SDK-agnostic helper.

Maps a raw platform state string onto the provider-neutral InstanceState enum.
Deliberately contains NO SDK code (it just normalizes strings), so it can live in
the interfaces-only phase and be reused by any concrete provider later.

Example (EC2): "pending"|"running"|"stopping"|"stopped"|"shutting-down"|
"terminated" → InstanceState.
"""

from app.aws.interfaces.types import InstanceState

# Common raw → neutral mappings (covers EC2's instance-state-name values;
# other platforms can extend this map).
_RAW_TO_STATE = {
    "pending": InstanceState.STARTING,
    "running": InstanceState.RUNNING,
    "stopping": InstanceState.STOPPING,
    "shutting-down": InstanceState.STOPPING,
    "shutting_down": InstanceState.STOPPING,
    "stopped": InstanceState.STOPPED,
    "terminated": InstanceState.UNKNOWN,
}


def map_instance_state(raw_state: str) -> InstanceState:
    """Normalize a raw platform state string to InstanceState (UNKNOWN if unmapped)."""
    if not raw_state:
        return InstanceState.UNKNOWN
    return _RAW_TO_STATE.get(raw_state.strip().lower(), InstanceState.UNKNOWN)
