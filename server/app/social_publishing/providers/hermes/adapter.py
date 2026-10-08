"""Hermes execution adapter (Phase 4).

The adapter is the SINGLE boundary between Trendzzo and the Hermes runtime.
All browser/session lifecycle, navigation, interaction, media upload, publish,
and verification calls go through this interface. Trendzzo business logic must
never talk to a browser directly.

Only a FakeHermesExecutionAdapter is provided here. It simulates outcomes for
tests and wiring validation and performs NO real browser actions.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

from app.social_publishing.providers.errors import PublishInstruction


@dataclass
class AdapterOutcome:
    """Low-level result of an adapter publish attempt."""

    kind: str                       # see FakeOutcome values
    external_post_id: Optional[str] = None
    external_url: Optional[str] = None
    detail: str = ""


@runtime_checkable
class HermesExecutionAdapter(Protocol):
    """Boundary to the Hermes runtime. Implementations own session lifecycle."""

    async def connect(self, instruction: PublishInstruction) -> None:
        """Start/attach to a Hermes browser session for this account."""
        ...

    async def publish(self, instruction: PublishInstruction) -> AdapterOutcome:
        """Perform compose → media → publish and return a low-level outcome."""
        ...

    async def verify(
        self, instruction: PublishInstruction
    ) -> AdapterOutcome:
        """Verify whether a prior (possibly uncertain) publish succeeded."""
        ...

    async def close(self) -> None:
        """Release the browser/session for this operation."""
        ...


class FakeOutcome(str, Enum):
    """Outcomes the fake adapter can simulate (Phase 27)."""

    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    AUTH_REQUIRED = "auth_required"
    ACTION_REQUIRED = "action_required"
    UNKNOWN = "unknown"
    VERIFICATION_FAILED = "verification_failed"


class FakeHermesExecutionAdapter:
    """
    Deterministic, side-effect-free adapter for tests and local dev.

    Configured with a scripted outcome; performs no browser actions. Records
    the calls made so tests can assert lifecycle ordering (connect→publish→
    verify→close).
    """

    def __init__(
        self,
        outcome: FakeOutcome = FakeOutcome.SUCCESS,
        *,
        verify_outcome: Optional[FakeOutcome] = None,
    ) -> None:
        self._outcome = outcome
        # When a publish returns UNKNOWN, the workflow verifies; this controls
        # what verification reports.
        self._verify_outcome = verify_outcome or FakeOutcome.SUCCESS
        self.calls: list[str] = []

    async def connect(self, instruction: PublishInstruction) -> None:
        self.calls.append("connect")

    async def publish(self, instruction: PublishInstruction) -> AdapterOutcome:
        self.calls.append("publish")
        if self._outcome == FakeOutcome.SUCCESS:
            return AdapterOutcome(
                kind=FakeOutcome.SUCCESS.value,
                external_post_id=f"fake_{instruction.operation_id}",
                external_url="https://example.test/p/fake",
            )
        return AdapterOutcome(kind=self._outcome.value, detail="simulated")

    async def verify(self, instruction: PublishInstruction) -> AdapterOutcome:
        self.calls.append("verify")
        if self._verify_outcome == FakeOutcome.SUCCESS:
            return AdapterOutcome(
                kind=FakeOutcome.SUCCESS.value,
                external_post_id=f"fake_{instruction.operation_id}",
                external_url="https://example.test/p/fake",
            )
        return AdapterOutcome(kind=self._verify_outcome.value, detail="verify-simulated")

    async def close(self) -> None:
        self.calls.append("close")
