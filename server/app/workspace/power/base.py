"""
PowerController — the strategy interface for controlling workspace power.

A PowerController abstracts *how* a workspace runtime is started, stopped, and
inspected — decoupling Trendzzo's orchestration logic from the concrete platform
(EC2, container, cluster, PaaS, …). This is the Strategy Pattern: callers depend
only on this interface; the concrete strategy is chosen at the edges via the
factory / dependency injection.

SOLID boundaries:
  • Single responsibility — a controller only starts/stops/inspects a runtime.
  • Open/closed — new platforms are added as new strategies, not by editing
    existing ones.
  • Liskov — every strategy honours the same 3-method contract + result shape.
  • Interface segregation — exactly three cohesive methods.
  • Dependency inversion — the app depends on this abstraction, never on a
    concrete SDK. No infrastructure SDKs (boto3, docker, kubernetes, …) are
    imported here or by any caller; only concrete strategies touch an SDK.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum


class PowerState(str, Enum):
    """Coarse power state a controller can report from status()."""

    RUNNING = "running"
    SLEEPING = "sleeping"
    STARTING = "starting"
    STOPPING = "stopping"
    UNKNOWN = "unknown"


class PowerController(ABC):
    """
    Strategy contract for workspace power control.

    Every method is async (real backends do network I/O) and returns a small,
    JSON-serializable result dict so orchestrators/endpoints can report outcomes
    uniformly across strategies.
    """

    #: Human-readable strategy name (overridden by each concrete controller).
    name: str = "base"

    @abstractmethod
    async def startup(self) -> dict:
        """Start the workspace runtime (bring it up)."""
        raise NotImplementedError

    @abstractmethod
    async def shutdown(self) -> dict:
        """Completely stop the workspace runtime (so the host can be halted)."""
        raise NotImplementedError

    @abstractmethod
    async def status(self) -> dict:
        """Report the current power state of the workspace runtime."""
        raise NotImplementedError

    # ── shared helper ───────────────────────────────────────────────────────
    def _result(
        self,
        action: str,
        *,
        ok: bool = True,
        state: PowerState = PowerState.UNKNOWN,
        implemented: bool = True,
        detail: str = "",
    ) -> dict:
        """Build the uniform result envelope returned by all strategies."""
        return {
            "provider": self.name,
            "action": action,
            "ok": ok,
            "implemented": implemented,
            "state": state.value,
            "detail": detail,
            "at": datetime.now(timezone.utc).isoformat(),
        }
