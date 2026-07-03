"""
SleepPolicy — the configurable rules that govern when a workspace may sleep.

Pure configuration value object. Holds the idle timeout and per-condition
toggles. Built from env-backed settings (config.py) so ops can tune behaviour
without code changes, but it can also be constructed directly in tests.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SleepPolicy:
    """Immutable snapshot of the sleep rules used for a single decision."""

    # Idle window: the workspace must have had no API activity for at least this
    # many seconds before sleep is permitted.
    idle_timeout_seconds: int = 900

    # Guards — when True, the corresponding active resource blocks sleep.
    block_on_websocket: bool = True
    block_on_ai: bool = True
    block_on_publishing: bool = True
    block_on_background_tasks: bool = True
    block_on_uploads: bool = True

    @classmethod
    def from_config(cls) -> "SleepPolicy":
        """Build a policy from the app's env-backed configuration."""
        # Imported lazily so this module has no import-time dependency on config.
        from app.config import (
            SLEEP_IDLE_TIMEOUT_SECONDS,
            SLEEP_BLOCK_ON_WEBSOCKET,
            SLEEP_BLOCK_ON_AI,
            SLEEP_BLOCK_ON_PUBLISHING,
            SLEEP_BLOCK_ON_BACKGROUND_TASKS,
            SLEEP_BLOCK_ON_UPLOADS,
        )

        return cls(
            idle_timeout_seconds=SLEEP_IDLE_TIMEOUT_SECONDS,
            block_on_websocket=SLEEP_BLOCK_ON_WEBSOCKET,
            block_on_ai=SLEEP_BLOCK_ON_AI,
            block_on_publishing=SLEEP_BLOCK_ON_PUBLISHING,
            block_on_background_tasks=SLEEP_BLOCK_ON_BACKGROUND_TASKS,
            block_on_uploads=SLEEP_BLOCK_ON_UPLOADS,
        )
