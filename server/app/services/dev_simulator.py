"""
Developer Simulator — Error simulation and sandbox control engine.
Only active in development mode. Invisible in production.

Allows toggling:
- API failure simulation
- AI timeout simulation
- Rate limit simulation
- Disconnected account simulation
- Slow response simulation
"""

import asyncio
from app.config import USE_MOCK

# In-memory simulation flags (reset on server restart)
_simulation_flags: dict = {
    "simulate_api_failure":       False,
    "simulate_ai_timeout":        False,
    "simulate_rate_limit":        False,
    "simulate_disconnected":      False,
    "simulate_slow_response":     False,
    "slow_response_delay_ms":     2000,
}


def get_simulation_flags() -> dict:
    """Return current simulation flags. Always empty dict in production."""
    if not USE_MOCK:
        return {}
    return dict(_simulation_flags)


def set_simulation_flag(flag: str, value) -> dict:
    """Set a simulation flag. No-op in production."""
    if not USE_MOCK:
        return {"error": "Simulation flags only available in development mode"}
    if flag not in _simulation_flags:
        return {"error": f"Unknown flag: {flag}"}
    _simulation_flags[flag] = value
    return {"flag": flag, "value": value, "status": "set"}


def reset_all_flags() -> dict:
    """Reset all simulation flags to default (off)."""
    if not USE_MOCK:
        return {"error": "Only available in development mode"}
    for key in _simulation_flags:
        if key == "slow_response_delay_ms":
            _simulation_flags[key] = 2000
        else:
            _simulation_flags[key] = False
    return {"status": "reset", "flags": dict(_simulation_flags)}


async def apply_simulation_middleware():
    """
    Call this at the start of any service function to apply active simulations.
    Raises exceptions or adds delays based on active flags.
    """
    if not USE_MOCK:
        return

    flags = _simulation_flags

    if flags.get("simulate_slow_response"):
        delay = flags.get("slow_response_delay_ms", 2000) / 1000
        await asyncio.sleep(delay)

    if flags.get("simulate_ai_timeout"):
        raise TimeoutError("[DEV] Simulated AI service timeout")

    if flags.get("simulate_api_failure"):
        raise ConnectionError("[DEV] Simulated API failure")

    if flags.get("simulate_rate_limit"):
        raise Exception("[DEV] Simulated rate limit — 429 Too Many Requests")


def get_sandbox_status() -> dict:
    """Return full sandbox status for the developer toolbar."""
    if not USE_MOCK:
        return {"mode": "production", "sandbox_active": False}

    return {
        "mode": "development",
        "sandbox_active": True,
        "simulation_flags": dict(_simulation_flags),
        "active_simulations": [k for k, v in _simulation_flags.items() if v is True],
        "mock_systems": [
            "content_generation",
            "trend_analysis",
            "social_presence",
            "platform_publishing",
            "analytics",
            "campaigns",
        ],
    }
