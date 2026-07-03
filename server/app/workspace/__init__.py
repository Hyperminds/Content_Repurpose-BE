"""
Workspace module.

Self-contained package for the AI-workspace power lifecycle
(sleep / start / stop / health). Exposes an APIRouter that main.py registers.

No AWS integration — the service holds an in-memory state machine that acts as
the single seam for a future real backend (EC2/Lambda control, EventBridge, etc).
"""

from app.workspace.workspace_router import router as workspace_router

__all__ = ["workspace_router"]
