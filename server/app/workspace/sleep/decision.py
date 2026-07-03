"""
SleepDecision — the immutable result of a sleep evaluation.

Serializes to the exact public contract:
    {"should_sleep": true,  "reason": "Workspace inactive for configured timeout"}
    {"should_sleep": false, "reason": "AI generation currently running"}

Carries an optional `signals` snapshot for observability/debugging, which is
only included in the verbose dict form.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class SleepDecision:
    """Outcome of the SleepValidator / SleepDecisionEngine."""

    should_sleep: bool
    reason: str
    signals: Optional[dict] = field(default=None)

    # ── Factories (read nicely at the call site) ───────────────────────────
    @classmethod
    def allow(cls, reason: str, signals: Optional[dict] = None) -> "SleepDecision":
        return cls(should_sleep=True, reason=reason, signals=signals)

    @classmethod
    def block(cls, reason: str, signals: Optional[dict] = None) -> "SleepDecision":
        return cls(should_sleep=False, reason=reason, signals=signals)

    # ── Serialization ───────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """Minimal public contract shape."""
        return {"should_sleep": self.should_sleep, "reason": self.reason}

    def to_verbose_dict(self) -> dict:
        """Contract shape plus the signal snapshot for diagnostics."""
        data = self.to_dict()
        if self.signals is not None:
            data["signals"] = self.signals
        return data
