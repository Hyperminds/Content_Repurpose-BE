"""
Workspace router.

Exposes the workspace power lifecycle endpoints:
    GET  /workspace/status   → current state + metadata
    POST /workspace/start    → begin waking the workspace
    POST /workspace/stop     → begin pausing the workspace
    GET  /workspace/health   → module health probe

The WorkspaceService is supplied via FastAPI dependency injection
(`Depends(get_workspace_service)`), keeping the routes thin and the business
logic testable/swappable. No AWS calls — service state is in-memory.
"""

from fastapi import APIRouter, Depends

from app.workspace.workspace_service import WorkspaceService, get_workspace_service
from app.workspace.workspace_schemas import (
    WorkspaceStatusResponse,
    WorkspaceActionResponse,
    WorkspaceHealthResponse,
    SleepDecisionResponse,
    WakeStatusResponse,
    WakeTriggerResponse,
    LifecycleStateResponse,
)
from app.workspace.sleep import SleepDecisionEngine, get_sleep_engine
from app.workspace.wake import WakeController, get_wake_controller
from app.workspace.lifecycle import get_lifecycle_manager

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("/status", response_model=WorkspaceStatusResponse)
async def get_workspace_status(
    service: WorkspaceService = Depends(get_workspace_service),
):
    """Return the current workspace state, last activity, and startup estimate."""
    snap = service.get_status()
    return WorkspaceStatusResponse(
        status=snap.state,
        # Minute precision (e.g. "2026-07-02T12:30") per the API contract.
        last_activity=snap.last_activity.strftime("%Y-%m-%dT%H:%M"),
        estimated_startup=snap.estimated_startup,
    )


@router.post("/start", response_model=WorkspaceActionResponse)
async def start_workspace(
    service: WorkspaceService = Depends(get_workspace_service),
):
    """Begin waking the workspace. Idempotent."""
    return WorkspaceActionResponse(status=service.start())


@router.post("/stop", response_model=WorkspaceActionResponse)
async def stop_workspace(
    service: WorkspaceService = Depends(get_workspace_service),
):
    """Begin pausing the workspace. Idempotent."""
    return WorkspaceActionResponse(status=service.stop())


@router.get("/health", response_model=WorkspaceHealthResponse)
async def workspace_health(
    service: WorkspaceService = Depends(get_workspace_service),
):
    """Health probe for the workspace module."""
    return WorkspaceHealthResponse(**service.health())


@router.get("/sleep-check", response_model=SleepDecisionResponse)
async def workspace_sleep_check(
    engine: SleepDecisionEngine = Depends(get_sleep_engine),
):
    """
    Evaluate whether the workspace is allowed to enter Sleep.

    Reads live activity signals (last API activity, WebSocket connections,
    running AI generations, publishing jobs, background tasks, pending uploads)
    and applies the configurable SleepPolicy. Decision only — this never stops
    any infrastructure.
    """
    decision = await engine.evaluate()
    return SleepDecisionResponse(**decision.to_dict())


@router.post("/wake", response_model=WakeTriggerResponse)
async def wake_workspace(
    controller: WakeController = Depends(get_wake_controller),
):
    """
    Wake a stopped workspace.

    Fire-and-forget: triggers PowerController.startup() (EC2 StartInstances in
    production) in the background and returns {"status": "starting"} immediately
    — it does NOT wait for the instance to become Running. The frontend Health
    Polling Service confirms readiness via GET /workspace/health.
    """
    return WakeTriggerResponse(**await controller.wake())


@router.get("/wake/status", response_model=WakeStatusResponse)
async def wake_status(
    controller: WakeController = Depends(get_wake_controller),
):
    """Report the current wake/power status of the workspace."""
    return WakeStatusResponse(**await controller.status())


@router.get("/lifecycle", response_model=LifecycleStateResponse)
async def workspace_lifecycle():
    """
    Current workspace lifecycle snapshot (STOPPED/STARTING/RUNNING/
    SHUTTING_DOWN/ERROR) plus allowed next states and recent transitions.
    Read-only — lifecycle transitions are driven by the manager, not this route.
    """
    return LifecycleStateResponse(**get_lifecycle_manager().snapshot())
