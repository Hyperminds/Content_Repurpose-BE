"""
Metering data models for TrendZZo Global Resource Metering.

Defines the canonical shape of a single metered request event.
Designed to be billing-provider agnostic so records can later be
forwarded to OpenMeter, Lago, or any usage-based billing system
without changing the capture logic.
"""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class AIUsage(BaseModel):
    """AI consumption attached to a request (optional)."""
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class MeteringRecord(BaseModel):
    """
    A single metered request event.

    This is the unit that gets persisted to MongoDB and, in future,
    streamed to an external metering/billing provider.
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    request_id: str = Field(..., description="Unique per-request UUID")
    organization_id: str = Field(default="default", description="Tenant / org identifier")
    user_id: Optional[str] = Field(default=None, description="Authenticated user id, if any")

    # ── Request descriptor ─────────────────────────────────────────────────────
    endpoint: str = Field(..., description="Request path, e.g. /campaigns")
    method: str = Field(..., description="HTTP method")
    status_code: int = Field(default=0, description="Response status code")

    # ── Timing ─────────────────────────────────────────────────────────────────
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    execution_time_ms: float = Field(default=0.0, description="Wall-clock handler time")

    # ── Payload sizes (bytes) ───────────────────────────────────────────────────
    request_bytes: int = Field(default=0, description="Inbound payload size")
    response_bytes: int = Field(default=0, description="Outbound payload size")
    upload_bytes: int = Field(default=0, description="Multipart/file upload size")
    download_bytes: int = Field(default=0, description="File download size served")

    # ── AI usage (optional) ──────────────────────────────────────────────────────
    ai_model: Optional[str] = None
    ai_prompt_tokens: int = 0
    ai_completion_tokens: int = 0
    ai_total_tokens: int = 0
    ai_estimated_cost_usd: float = 0.0

    # ── Meta ───────────────────────────────────────────────────────────────────
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None
    # Flag for future export pipelines (OpenMeter/Lago). False = not yet shipped.
    exported: bool = False

    def to_mongo(self) -> dict:
        """Serialize to a MongoDB-insertable dict (keeps native datetime)."""
        return self.model_dump()


def serialize_metering_record(doc: dict) -> dict:
    """Convert a MongoDB metering doc into a JSON-safe dict for API responses."""
    ts = doc.get("timestamp")
    return {
        "id": str(doc.get("_id")) if doc.get("_id") else None,
        "request_id": doc.get("request_id"),
        "organization_id": doc.get("organization_id"),
        "user_id": doc.get("user_id"),
        "endpoint": doc.get("endpoint"),
        "method": doc.get("method"),
        "status_code": doc.get("status_code"),
        "timestamp": ts.isoformat() if isinstance(ts, datetime) else ts,
        "execution_time_ms": doc.get("execution_time_ms", 0.0),
        "request_bytes": doc.get("request_bytes", 0),
        "response_bytes": doc.get("response_bytes", 0),
        "upload_bytes": doc.get("upload_bytes", 0),
        "download_bytes": doc.get("download_bytes", 0),
        "ai_model": doc.get("ai_model"),
        "ai_prompt_tokens": doc.get("ai_prompt_tokens", 0),
        "ai_completion_tokens": doc.get("ai_completion_tokens", 0),
        "ai_total_tokens": doc.get("ai_total_tokens", 0),
        "ai_estimated_cost_usd": doc.get("ai_estimated_cost_usd", 0.0),
        "client_ip": doc.get("client_ip"),
        "user_agent": doc.get("user_agent"),
        "exported": doc.get("exported", False),
    }
