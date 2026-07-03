"""
Workspace API schemas.

Pydantic response models for the workspace endpoints. Kept separate from the
domain models (workspace_models.py) so the wire contract can evolve
independently of internal state representation.
"""

from pydantic import BaseModel, Field

from app.workspace.workspace_models import WorkspaceState


class WorkspaceStatusResponse(BaseModel):
    """GET /workspace/status"""

    status: WorkspaceState = Field(..., description="Current workspace state")
    last_activity: str = Field(
        ...,
        description="ISO-8601 timestamp of the last activity (minute precision)",
        examples=["2026-07-02T12:30"],
    )
    estimated_startup: int = Field(
        ..., description="Estimated cold-start time in seconds", examples=[30]
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "status": "sleeping",
                "last_activity": "2026-07-02T12:30",
                "estimated_startup": 30,
            }
        }
    }


class WorkspaceActionResponse(BaseModel):
    """POST /workspace/start and POST /workspace/stop"""

    status: WorkspaceState = Field(..., description="Resulting workspace state")

    model_config = {"json_schema_extra": {"example": {"status": "starting"}}}


class WorkspaceHealthResponse(BaseModel):
    """GET /workspace/health"""

    status: str = Field(default="healthy", description="Workspace module health")

    model_config = {"json_schema_extra": {"example": {"status": "healthy"}}}


class SleepDecisionResponse(BaseModel):
    """GET /workspace/sleep-check — output of the Sleep Decision Engine."""

    should_sleep: bool = Field(..., description="Whether the workspace may sleep")
    reason: str = Field(..., description="Human-readable explanation of the decision")

    model_config = {
        "json_schema_extra": {
            "example": {
                "should_sleep": False,
                "reason": "AI generation currently running",
            }
        }
    }


class WakeStatusResponse(BaseModel):
    """GET /workspace/wake/status — current wake/power status."""

    state: str = Field(..., description="Coarse power state: running|sleeping|starting|stopping|unknown")
    label: str = Field(..., description="Public label: Running|Stopped|Pending|Stopping|…")
    is_awake: bool = Field(..., description="True once the workspace is fully running")
    is_waking: bool = Field(..., description="True while transitioning up (e.g. EC2 Pending)")
    provider: str = Field(..., description="Which PowerController answered (local|ec2|…)")
    detail: str = Field(default="", description="Human-readable detail")
    ok: bool = Field(default=True, description="False if the power action reported an error")

    model_config = {
        "json_schema_extra": {
            "example": {
                "state": "running",
                "label": "Running",
                "is_awake": True,
                "is_waking": False,
                "provider": "ec2",
                "detail": "EC2 instance i-0abc… is Running",
                "ok": True,
            }
        }
    }


class WakeTriggerResponse(BaseModel):
    """POST /workspace/wake — fire-and-forget startup acknowledgement."""

    status: str = Field(default="starting", description="Always 'starting' — readiness is confirmed via health polling")

    model_config = {"json_schema_extra": {"example": {"status": "starting"}}}


class LifecycleStateResponse(BaseModel):
    """GET /workspace/lifecycle — current lifecycle snapshot."""

    state: str = Field(..., description="STOPPED|STARTING|RUNNING|SHUTTING_DOWN|ERROR")
    since: str = Field(..., description="ISO timestamp of the last state change")
    allowed_next: list[str] = Field(default_factory=list, description="Legal next states")
    provider: str = Field(default="unknown", description="Active PowerController strategy")
    error: str | None = Field(default=None, description="Last error, if in ERROR state")
    recent_transitions: list[dict] = Field(default_factory=list, description="Recent transition history")

    model_config = {
        "json_schema_extra": {
            "example": {
                "state": "RUNNING",
                "since": "2026-07-02T12:30:00+00:00",
                "allowed_next": ["ERROR", "SHUTTING_DOWN"],
                "provider": "local",
                "error": None,
                "recent_transitions": [],
            }
        }
    }
