"""
ShutdownDecision — immutable result of a shutdown evaluation.

Serializes to {"allowed": bool, "reason": str}.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ShutdownDecision:
    allowed: bool
    reason: str

    @classmethod
    def allow(cls, reason: str) -> "ShutdownDecision":
        return cls(True, reason)

    @classmethod
    def block(cls, reason: str) -> "ShutdownDecision":
        return cls(False, reason)

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason}
