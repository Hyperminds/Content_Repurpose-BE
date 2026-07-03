"""
Backward-compatibility shim.

The PowerController architecture moved to the dedicated Strategy-Pattern package
`app.workspace.power` (interface + LocalPowerController + future stubs + factory).
This module re-exports the public names so existing imports
(`from app.workspace.sleep.power_controller import ...`) keep working.

Prefer importing from `app.workspace.power` in new code.
"""

from app.workspace.power import (
    PowerController,
    PowerState,
    LocalPowerController,
    get_power_controller,
)

# Historical name: the original default was a log-only "no-op" controller.
# LocalPowerController is its successor; keep the alias for compatibility.
NoOpPowerController = LocalPowerController

__all__ = [
    "PowerController",
    "PowerState",
    "LocalPowerController",
    "NoOpPowerController",
    "get_power_controller",
]
