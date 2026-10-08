"""Process-wide registry of LIVE user-assisted browser sessions (Hermes).

Optimization for the two-phase prepare → confirm flow: instead of launching a
fresh browser and RE-RUNNING the whole prepare again during confirm (a second
Chromium launch + navigate + re-open composer + re-attach media), prepare can
KEEP its prepared browser alive and stash it here. confirm then reuses that
exact live composer and goes straight to the Post/Share click — roughly halving
the slow browser work per publish.

Design constraints (all enforced here):
  - ONE live context per browser PROFILE DIR. Chromium locks the persistent
    profile dir, so two overlapping contexts on the same profile fail. put()
    evicts+closes any existing live session on the same profile_key before
    registering a new one, and callers evict before any fallback relaunch.
  - BOUNDED lifetime. A cached browser must never outlive its pending
    confirmation. Entries carry created_at + last_used; sweep() closes any entry
    past the hard TTL or the idle timeout. The ConfirmationExpiryWorker calls
    sweep() on its existing cadence, and also evicts entries whose confirmation
    reached a terminal/expired state.
  - BEST-EFFORT. This is a pure latency optimization: a miss (different worker
    process, evicted/expired entry, dead composer) MUST fall back to the normal
    launch + re-prepare path. Nothing here is required for correctness.

In-process only: works because the app runs a single uvicorn worker (prepare and
confirm hit the same process). Under multiple workers a confirm may land on a
different process → cache miss → safe fallback. No cross-process coordination.

Stores NO credentials/cookies/tokens — only a live adapter handle the service
already created. Thread/async: the app runs one event loop; access is from
request handlers and the expiry worker on that loop.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Optional

from app.services.logger import log


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Hard cap on how long a prepared browser may stay alive (must be <= the pending
# confirmation TTL of 900s so a browser never outlives its confirmation).
LIVE_SESSION_TTL_S = _f("HERMES_LIVE_SESSION_TTL_S", 600)
# Idle timeout: close a prepared browser if the user hasn't confirmed within this
# window of inactivity (so Chromium doesn't sit open when the user walks away).
LIVE_SESSION_IDLE_S = _f("HERMES_LIVE_SESSION_IDLE_S", 300)


@dataclass
class _LiveSession:
    adapter: object
    profile_key: str
    platform: str
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)


class LiveSessionCache:
    """Process-wide live-adapter registry keyed by confirmation_id."""

    def __init__(self) -> None:
        self._by_confirmation: dict[str, _LiveSession] = {}

    # ── Registration ────────────────────────────────────────────────────────
    async def put(
        self, confirmation_id: str, adapter: object, profile_key: str, platform: str
    ) -> None:
        """
        Register a live prepared adapter for a confirmation.

        Enforces one-live-context-per-profile: any existing live session on the
        same profile_key (including a stale one for this confirmation) is
        closed first so Chromium's profile-dir lock is never contended.
        """
        await self._evict_profile(profile_key, keep_confirmation=confirmation_id)
        existing = self._by_confirmation.pop(confirmation_id, None)
        if existing is not None and existing.adapter is not adapter:
            await _safe_close(existing.adapter)
        self._by_confirmation[confirmation_id] = _LiveSession(
            adapter=adapter, profile_key=profile_key, platform=platform
        )

    # ── Reuse ───────────────────────────────────────────────────────────────
    def get(self, confirmation_id: str) -> Optional[object]:
        """Return the live adapter for a confirmation (and mark it used), or None."""
        entry = self._by_confirmation.get(confirmation_id)
        if entry is None:
            return None
        entry.last_used = time.monotonic()
        return entry.adapter

    # ── Eviction ──────────────────────────────────────────────────────────────
    async def evict(self, confirmation_id: str, *, close: bool = True) -> None:
        """Remove a confirmation's live session, closing the browser by default."""
        entry = self._by_confirmation.pop(confirmation_id, None)
        if entry is not None and close:
            await _safe_close(entry.adapter)

    async def _evict_profile(self, profile_key: str, *, keep_confirmation: str = "") -> None:
        """Close+remove any live session on a profile_key (except keep_confirmation)."""
        for cid, entry in list(self._by_confirmation.items()):
            if entry.profile_key == profile_key and cid != keep_confirmation:
                self._by_confirmation.pop(cid, None)
                await _safe_close(entry.adapter)

    async def sweep(self) -> int:
        """
        Close+evict entries past the hard TTL or idle timeout. Returns the count
        evicted. Called by the ConfirmationExpiryWorker on its existing cadence
        so prepared browsers never leak when a user never confirms.
        """
        now = time.monotonic()
        evicted = 0
        for cid, entry in list(self._by_confirmation.items()):
            age = now - entry.created_at
            idle = now - entry.last_used
            if age >= LIVE_SESSION_TTL_S or idle >= LIVE_SESSION_IDLE_S:
                self._by_confirmation.pop(cid, None)
                await _safe_close(entry.adapter)
                evicted += 1
        if evicted:
            log.info("Hermes live sessions swept", evicted=evicted)
        return evicted

    def __len__(self) -> int:
        return len(self._by_confirmation)


async def _safe_close(adapter: object) -> None:
    close = getattr(adapter, "close", None)
    if not callable(close):
        return
    try:
        await close()
    except Exception:
        # Best-effort: a browser that failed to close must not break the caller.
        pass


# Process-wide singleton shared by every user-assisted service + the expiry
# worker (all in one process / event loop).
live_session_cache = LiveSessionCache()
