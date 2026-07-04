"""
Activity Tracking Middleware.

Updates the caller's `last_activity` marker in MongoDB on every SUCCESSFUL
request, so the app knows when a user (or the workspace) was last active.

Behaviour
---------
• Tracks the standard mutating/read verbs: GET, POST, PUT, DELETE, PATCH.
• Ignores probe/asset/doc paths: /health, /static, /docs (plus /redoc,
  /openapi.json and CORS preflight OPTIONS).
• Only records on success (2xx/3xx) — failed requests don't count as activity.
• Asynchronous + minimal latency: the DB write is offloaded to the
  ActivityService (fire-and-forget), never awaited on the request path.
• Fail-safe: any error is swallowed. Activity tracking never alters the request
  or the response, and never changes business logic.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

from app.services.activity_service import get_activity_service
from app.utils import metering_utils as mu

# Verbs that count as activity.
_TRACKED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}

# Path prefixes that are never treated as user activity: health/asset/doc probes,
# plus the workspace monitoring endpoints themselves (status/health/sleep-check).
# Excluding the latter is important — otherwise a sleep-decision poller would
# generate activity and keep the workspace awake forever. Note: /workspace/start
# and /workspace/stop are deliberately NOT ignored (those are real user actions).
_IGNORED_PREFIXES = (
    "/health",
    "/static",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/workspace/status",
    "/workspace/health",
    "/workspace/sleep-check",
)


def _should_track(request: Request) -> bool:
    if request.method not in _TRACKED_METHODS:
        return False
    path = request.url.path
    if path.startswith(_IGNORED_PREFIXES):
        return False
    # Ignore bot/scanner probes — these paths are never legitimate user activity
    # and would keep the idle timer alive indefinitely.
    _SCAN_PATTERNS = (
        ".env", ".php", "config", "wp-", "admin", "login",
        "next.config", "nuxt.config", "package.json", "craco",
        "actuator", "phpinfo", ".git", "setup.php",
    )
    path_lower = path.lower()
    if any(p in path_lower for p in _SCAN_PATTERNS):
        return False
    return True


class ActivityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Fast bail-out for ignored paths / untracked verbs.
        if not _should_track(request):
            return await call_next(request)

        response = await call_next(request)

        # Only authenticated successful responses count as real activity.
        # Bot/scanner requests get 404s and have no user_id — both conditions
        # must be true so anonymous traffic never resets the idle timer.
        try:
            status_code = getattr(response, "status_code", 500)
            if status_code < 400:
                identity = mu.extract_identity(request)
                if identity["user_id"] is not None:
                    get_activity_service().track(
                        user_id=identity["user_id"],
                        organization_id=identity["organization_id"],
                        path=request.url.path,
                        method=request.method,
                    )
        except Exception:
            pass

        return response
