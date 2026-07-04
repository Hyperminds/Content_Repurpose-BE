"""
Centralized WebSocket Connection Manager for TrendZo.
Supports realtime updates for:
- Analytics, campaigns, trends, AI generation, notifications, dashboard activity.

Scalable architecture — can be backed by Redis pub/sub for horizontal scaling.
"""

import asyncio
import json
from typing import Dict, Set
from datetime import datetime, timezone
from fastapi import WebSocket, WebSocketDisconnect


class ConnectionManager:
    """
    Manages WebSocket connections per user and per channel.
    Channels: analytics, campaigns, trends, ai_activity, notifications, dashboard
    """

    def __init__(self):
        # user_id -> set of WebSocket connections
        self._user_connections: Dict[str, Set[WebSocket]] = {}
        # channel -> set of user_ids subscribed
        self._channel_subscribers: Dict[str, Set[str]] = {}
        # All active connections for broadcast
        self._all_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket, user_id: str, channels: list = None):
        """Accept connection and register user + channels."""
        await websocket.accept()
        self._all_connections.add(websocket)

        if user_id not in self._user_connections:
            self._user_connections[user_id] = set()
        self._user_connections[user_id].add(websocket)

        # Subscribe to channels
        for channel in (channels or ["dashboard"]):
            if channel not in self._channel_subscribers:
                self._channel_subscribers[channel] = set()
            self._channel_subscribers[channel].add(user_id)

    def disconnect(self, websocket: WebSocket, user_id: str):
        """Remove connection and clean up subscriptions."""
        self._all_connections.discard(websocket)

        if user_id in self._user_connections:
            self._user_connections[user_id].discard(websocket)
            if not self._user_connections[user_id]:
                del self._user_connections[user_id]
                # Remove from all channels
                for channel in self._channel_subscribers:
                    self._channel_subscribers[channel].discard(user_id)

    async def send_to_user(self, user_id: str, event: str, data: dict):
        """Send a message to all connections of a specific user."""
        message = json.dumps({
            "event": event,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        connections = self._user_connections.get(user_id, set()).copy()
        for ws in connections:
            try:
                await ws.send_text(message)
            except Exception:
                self._user_connections.get(user_id, set()).discard(ws)
                self._all_connections.discard(ws)

    async def send_to_channel(self, channel: str, event: str, data: dict):
        """Send a message to all users subscribed to a channel."""
        subscribers = self._channel_subscribers.get(channel, set()).copy()
        for user_id in subscribers:
            await self.send_to_user(user_id, event, data)

    async def broadcast(self, event: str, data: dict):
        """Send a message to ALL connected users."""
        message = json.dumps({
            "event": event,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        dead = set()
        for ws in self._all_connections.copy():
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        self._all_connections -= dead

    async def close_all(self, code: int = 1001, reason: str = "Server shutting down") -> int:
        """
        Gracefully close every active WebSocket session and clear all registries.

        Used by the Sleep Orchestrator during shutdown. Best-effort: each close
        is guarded so one failure can't stop the rest. Returns the number of
        connections that were closed. (1001 = "going away".)
        """
        connections = list(self._all_connections)
        closed = 0
        for ws in connections:
            try:
                await ws.close(code=code, reason=reason)
                closed += 1
            except Exception:
                pass
        self._all_connections.clear()
        self._user_connections.clear()
        self._channel_subscribers.clear()
        return closed

    @property
    def active_connections(self) -> int:
        return len(self._all_connections)

    @property
    def active_users(self) -> int:
        return len(self._user_connections)

    def get_stats(self) -> dict:
        return {
            "active_connections": self.active_connections,
            "active_users": self.active_users,
            "channels": {ch: len(subs) for ch, subs in self._channel_subscribers.items()},
        }


# Global singleton instance
ws_manager = ConnectionManager()
