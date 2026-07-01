"""
Shared identity helpers.

Small, dependency-free utilities for resolving tenant/user identity from a
decoded JWT payload. Centralized here so routes don't each re-implement the
same fallback chain.
"""

from typing import Optional


def org_id_from_user(user: dict) -> str:
    """
    Resolve the caller's organization id from a decoded JWT payload.

    Falls back to user_id, then to "default", so it is safe for tokens that do
    not (yet) carry an organization claim.
    """
    return (
        user.get("organization_id")
        or user.get("org_id")
        or user.get("user_id")
        or "default"
    )


def user_id_from_user(user: dict) -> Optional[str]:
    """Resolve the user id from a decoded JWT payload."""
    return user.get("user_id")
