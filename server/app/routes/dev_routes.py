"""
Developer Toolbar Routes — DEVELOPMENT MODE ONLY.
These endpoints are completely disabled in staging and production.
They allow the developer toolbar to control sandbox behavior.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Any
from app.config import USE_MOCK, APP_ENV
from app.utils.jwt_handler import get_current_user

router = APIRouter(prefix="/dev", tags=["Developer Sandbox"])


def _require_dev_mode():
    """Raise 403 if not in development mode."""
    if not USE_MOCK:
        raise HTTPException(
            status_code=403,
            detail=f"Developer endpoints are disabled in {APP_ENV} mode."
        )


class SimulationFlagRequest(BaseModel):
    flag: str
    value: Any


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_dev_status(current_user: dict = Depends(get_current_user)):
    """Return current sandbox status and active simulation flags."""
    _require_dev_mode()
    from app.services.dev_simulator import get_sandbox_status
    return get_sandbox_status()


# ── Simulation flag control ───────────────────────────────────────────────────

@router.post("/simulate")
async def set_simulation(
    request: SimulationFlagRequest,
    current_user: dict = Depends(get_current_user),
):
    """Toggle a simulation flag on or off."""
    _require_dev_mode()
    from app.services.dev_simulator import set_simulation_flag
    result = set_simulation_flag(request.flag, request.value)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/reset")
async def reset_simulations(current_user: dict = Depends(get_current_user)):
    """Reset all simulation flags to default (all off)."""
    _require_dev_mode()
    from app.services.dev_simulator import reset_all_flags
    return reset_all_flags()


# ── Mock data refresh ─────────────────────────────────────────────────────────

@router.post("/refresh/trends")
async def refresh_mock_trends(current_user: dict = Depends(get_current_user)):
    """Force regenerate mock trend data (rotates to new random data)."""
    _require_dev_mode()
    from app.mock_data.trends import get_mock_full_trend_analysis
    import random
    categories = ["AI", "Technology", "Startups", "Finance", "Marketing"]
    platforms = ["twitter", "reddit", "linkedin", "instagram", "medium", "quora"]
    result = get_mock_full_trend_analysis(random.choice(categories), platforms)
    return {"status": "refreshed", "preview": result["insights"]["trend_headline"]}


@router.post("/refresh/analytics")
async def refresh_mock_analytics(current_user: dict = Depends(get_current_user)):
    """Force regenerate mock analytics data."""
    _require_dev_mode()
    from app.mock_data.analytics import (
        get_mock_publishing_stats, get_mock_ai_usage_summary,
        get_mock_platform_performance,
    )
    return {
        "status": "refreshed",
        "stats": get_mock_publishing_stats(),
        "ai_usage": get_mock_ai_usage_summary(),
        "platform_performance": get_mock_platform_performance(),
    }


@router.post("/refresh/content")
async def refresh_mock_content(current_user: dict = Depends(get_current_user)):
    """Force regenerate mock content generation data."""
    _require_dev_mode()
    from app.mock_data.content_generation import get_mock_content
    result = get_mock_content("Sample topic for refresh")
    return {
        "status": "refreshed",
        "preview": {
            "linkedin": result["linkedin"][:120] + "...",
            "twitter": result["twitter"],
        }
    }


# ── Simulation presets ────────────────────────────────────────────────────────

@router.post("/preset/all-failures")
async def preset_all_failures(current_user: dict = Depends(get_current_user)):
    """Activate all failure simulations at once for edge case testing."""
    _require_dev_mode()
    from app.services.dev_simulator import set_simulation_flag
    flags = ["simulate_api_failure", "simulate_rate_limit"]
    for flag in flags:
        set_simulation_flag(flag, True)
    return {"status": "preset_active", "preset": "all-failures", "active_flags": flags}


@router.post("/preset/slow-network")
async def preset_slow_network(current_user: dict = Depends(get_current_user)):
    """Simulate slow network conditions."""
    _require_dev_mode()
    from app.services.dev_simulator import set_simulation_flag
    set_simulation_flag("simulate_slow_response", True)
    set_simulation_flag("slow_response_delay_ms", 3000)
    return {"status": "preset_active", "preset": "slow-network", "delay_ms": 3000}


@router.post("/preset/normal")
async def preset_normal(current_user: dict = Depends(get_current_user)):
    """Reset to normal development mode (no simulations)."""
    _require_dev_mode()
    from app.services.dev_simulator import reset_all_flags
    return {"status": "preset_active", "preset": "normal", **reset_all_flags()}
