"""
Workspace Wake package.

A lightweight, strategy-agnostic service for waking the workspace when it has
been stopped. Talks to whatever PowerController the factory provides
(LocalPowerController now, EC2PowerController later — via POWER_CONTROLLER).

    WakeStatus     — normalized power/wake state value object
    WakeService    — wake business logic (delegates to a PowerController)
    WakeController — thin request-layer coordinator for the endpoints
"""

from app.workspace.wake.status import WakeStatus
from app.workspace.wake.service import WakeService, get_wake_service
from app.workspace.wake.controller import WakeController, get_wake_controller

__all__ = [
    "WakeStatus",
    "WakeService",
    "get_wake_service",
    "WakeController",
    "get_wake_controller",
]
