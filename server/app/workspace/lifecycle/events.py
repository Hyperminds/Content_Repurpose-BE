"""
LifecycleEvents — the observable events of the workspace lifecycle + a bus that
notifies both in-process subscribers and the frontend (over WebSocket).

The bus is transport-decoupled from callers: the manager just emits events; the
bus fans them out to any registered listener and broadcasts to connected clients
on the "workspace_lifecycle" channel. Broadcasting is best-effort and never
raises into the manager.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, List, Optional

from app.services.logger import log


class LifecycleEventType(str, Enum):
    STATE_CHANGED = "state_changed"
    STARTUP_REQUESTED = "startup_requested"
    STARTUP_COMPLETED = "startup_completed"
    SHUTDOWN_REQUESTED = "shutdown_requested"
    SHUTDOWN_COMPLETED = "shutdown_completed"
    ERROR = "error"


@dataclass
class LifecycleEvent:
    type: LifecycleEventType
    state: str
    previous_state: Optional[str] = None
    reason: str = ""
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "state": self.state,
            "previous_state": self.previous_state,
            "reason": self.reason,
            "at": self.at,
        }


class LifecycleEventBus:
    """Fans lifecycle events out to subscribers + connected WebSocket clients."""

    #: Channel clients subscribe to for lifecycle updates.
    CHANNEL = "workspace_lifecycle"

    def __init__(self) -> None:
        self._subscribers: List[Callable[[LifecycleEvent], None]] = []

    def subscribe(self, callback: Callable[[LifecycleEvent], None]) -> Callable[[], None]:
        """Register a listener. Returns an unsubscribe function."""
        self._subscribers.append(callback)

        def _unsub():
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return _unsub

    async def emit(self, event: LifecycleEvent) -> None:
        """Notify local subscribers, then broadcast to the frontend. Never raises."""
        for cb in list(self._subscribers):
            try:
                cb(event)
            except Exception as e:  # pragma: no cover - listener isolation
                log.error(f"[lifecycle] subscriber error: {e}")
        await self._broadcast(event)

    async def _broadcast(self, event: LifecycleEvent) -> None:
        try:
            from app.ws.manager import ws_manager
            await ws_manager.broadcast(self.CHANNEL, event.to_dict())
        except Exception:
            # WebSocket layer unavailable / no clients — non-fatal.
            pass
