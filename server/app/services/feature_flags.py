"""
Feature Flag System for TrendZo.
Allows safe rollout and testing of features without code deploys.

Flags can be controlled via:
- Environment variables (FEATURE_*)
- Database (for per-user flags in future)
- Admin API (for runtime toggling)
"""

import os
from app.config import APP_ENV, IS_PRODUCTION

# ── Default feature flags ─────────────────────────────────────────────────────
# Override any flag with env var: FEATURE_<FLAG_NAME>=true/false

_DEFAULT_FLAGS = {
    # AI features
    "ENABLE_AI_GENERATION":     True,
    "ENABLE_AI_IMAGES":         False,  # Not yet implemented
    "ENABLE_AI_VIDEO":          False,  # Future feature

    # Platform features
    "ENABLE_TRENDS":            True,
    "ENABLE_PROFILE_AUDIT":     True,
    "ENABLE_GROWTH_STRATEGIST": True,
    "ENABLE_CAMPAIGNS":         True,
    "ENABLE_PUBLISHING":        True,

    # System features
    "ENABLE_WEBSOCKETS":        True,
    "ENABLE_NOTIFICATIONS":     True,
    "ENABLE_ANALYTICS":         True,
    "ENABLE_RATE_LIMITING":     IS_PRODUCTION,

    # Experimental
    "ENABLE_MULTI_LANGUAGE":    False,
    "ENABLE_TEAM_COLLABORATION":False,
    "ENABLE_API_KEYS":          False,
}

# ── Runtime flag overrides (set via admin API) ────────────────────────────────
_runtime_overrides: dict = {}


def get_flag(flag_name: str) -> bool:
    """
    Get a feature flag value.
    Priority: runtime override > env var > default
    """
    # Check runtime overrides first
    if flag_name in _runtime_overrides:
        return _runtime_overrides[flag_name]

    # Check environment variable
    env_key = f"FEATURE_{flag_name}"
    env_val = os.getenv(env_key)
    if env_val is not None:
        return env_val.lower() in ("true", "1", "yes", "on")

    # Fall back to default
    return _DEFAULT_FLAGS.get(flag_name, False)


def set_flag(flag_name: str, value: bool):
    """Set a runtime flag override (resets on server restart)."""
    _runtime_overrides[flag_name] = value


def reset_flag(flag_name: str):
    """Remove a runtime override, reverting to env/default."""
    _runtime_overrides.pop(flag_name, None)


def get_all_flags() -> dict:
    """Get all flags with their current effective values."""
    all_flags = {}
    for flag_name in _DEFAULT_FLAGS:
        all_flags[flag_name] = {
            "value": get_flag(flag_name),
            "default": _DEFAULT_FLAGS[flag_name],
            "overridden": flag_name in _runtime_overrides,
            "env_set": os.getenv(f"FEATURE_{flag_name}") is not None,
        }
    return all_flags


def require_flag(flag_name: str):
    """
    Dependency for FastAPI routes.
    Usage: @router.get("/endpoint", dependencies=[Depends(require_flag("ENABLE_TRENDS"))])
    """
    from fastapi import HTTPException

    def _check():
        if not get_flag(flag_name):
            raise HTTPException(
                status_code=503,
                detail=f"Feature '{flag_name}' is currently disabled.",
            )
    return _check
