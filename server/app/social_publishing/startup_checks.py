"""Startup validation — checks critical configuration before the app accepts traffic.

Called during the lifespan startup. In production, missing configuration
causes the app to fail fast with a clear error message rather than silently
malfunctioning at runtime.
"""

import os

from app.config import IS_PRODUCTION, APP_ENV
from app.services.logger import log


def validate_environment() -> list[str]:
    """
    Validate that required environment variables are configured.

    Returns a list of warning/error messages. In production, missing critical
    vars are errors. In development, they're warnings.
    """
    issues: list[str] = []

    # Critical for production
    _check_var("SP_CREDENTIAL_ENCRYPTION_KEY", required_in_prod=True, issues=issues)

    # OAuth credentials (at least one platform should be configured)
    platforms_configured = 0
    if os.getenv("LINKEDIN_CLIENT_ID"):
        platforms_configured += 1
    if os.getenv("FACEBOOK_APP_ID"):
        platforms_configured += 1
    if os.getenv("INSTAGRAM_APP_ID"):
        platforms_configured += 1

    if platforms_configured == 0:
        issues.append(
            "WARNING: No social platform OAuth credentials configured. "
            "Set LINKEDIN_CLIENT_ID, FACEBOOK_APP_ID, or INSTAGRAM_APP_ID."
        )

    # MongoDB
    mongo_url = os.getenv("MONGODB_URL", "")
    if IS_PRODUCTION and "localhost" in mongo_url:
        issues.append("ERROR: MONGODB_URL points to localhost in production")

    if IS_PRODUCTION and "+srv" not in mongo_url and "tls=true" not in mongo_url:
        issues.append("WARNING: MONGODB_URL may not use TLS in production")

    # Frontend URL
    frontend = os.getenv("FRONTEND_URL", "")
    if IS_PRODUCTION and frontend.startswith("http://"):
        issues.append("WARNING: FRONTEND_URL uses HTTP in production (should be HTTPS)")

    return issues


def run_startup_checks() -> None:
    """Run all startup checks and log results."""
    issues = validate_environment()

    for issue in issues:
        if issue.startswith("ERROR"):
            log.error(f"Startup check: {issue}")
        else:
            log.warning(f"Startup check: {issue}")

    errors = [i for i in issues if i.startswith("ERROR")]
    if errors and IS_PRODUCTION:
        raise RuntimeError(
            f"Social Publishing Engine startup failed: {len(errors)} critical error(s). "
            f"First: {errors[0]}"
        )

    if not issues:
        log.info("Social Publishing startup checks passed", env=APP_ENV)


def _check_var(name: str, required_in_prod: bool, issues: list[str]) -> None:
    """Check if an environment variable is set."""
    value = os.getenv(name)
    if not value:
        if required_in_prod and IS_PRODUCTION:
            issues.append(f"ERROR: {name} is required in production but not set")
        elif required_in_prod:
            issues.append(f"WARNING: {name} not set (required in production)")
