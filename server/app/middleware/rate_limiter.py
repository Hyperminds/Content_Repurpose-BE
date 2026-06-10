"""
Rate Limiting Middleware for TrendZo.
Protects expensive endpoints (AI generation, websockets) from abuse.

Uses in-memory sliding window. For production horizontal scaling,
replace with Redis-backed rate limiter (e.g., slowapi + redis).
"""

import time
from collections import defaultdict
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import IS_DEVELOPMENT


# ── In-memory rate limit store ────────────────────────────────────────────────
# Format: {key: [(timestamp, ...)] }
_request_log: dict = defaultdict(list)

# ── Rate limit configs per path prefix ────────────────────────────────────────
RATE_LIMITS = {
    "/generate":              {"requests": 10,  "window_seconds": 60},
    "/social-presence/analyze": {"requests": 5,   "window_seconds": 60},
    "/trends/fetch":          {"requests": 8,   "window_seconds": 60},
    "/social-presence/competitor": {"requests": 5, "window_seconds": 60},
    "/social-presence/growth":    {"requests": 5, "window_seconds": 60},
    "/social-presence/brand":     {"requests": 5, "window_seconds": 60},
    "/social-presence/content-strategy": {"requests": 5, "window_seconds": 60},
    "/social-presence/bio":       {"requests": 8, "window_seconds": 60},
}

# Default for all other endpoints
DEFAULT_LIMIT = {"requests": 60, "window_seconds": 60}


def _get_limit_for_path(path: str) -> dict:
    """Find the most specific rate limit config for a path."""
    for prefix, config in RATE_LIMITS.items():
        if path.startswith(prefix):
            return config
    return DEFAULT_LIMIT


def _get_client_key(request: Request) -> str:
    """Generate a unique key per client (IP + user if available)."""
    ip = request.client.host if request.client else "unknown"
    # Try to get user_id from auth header (lightweight check)
    auth = request.headers.get("authorization", "")
    if auth:
        # Use last 8 chars of token as user identifier
        return f"{ip}:{auth[-8:]}"
    return ip


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding window rate limiter.
    Skipped entirely in development mode.
    """

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting in development
        if IS_DEVELOPMENT:
            return await call_next(request)

        path = request.url.path
        method = request.method

        # Always pass through OPTIONS (CORS preflight) and HEAD
        if method in ("OPTIONS", "HEAD", "GET"):
            return await call_next(request)

        limit_config = _get_limit_for_path(path)
        max_requests = limit_config["requests"]
        window = limit_config["window_seconds"]

        client_key = f"{_get_client_key(request)}:{path}"
        now = time.time()

        # Clean old entries outside the window
        _request_log[client_key] = [
            ts for ts in _request_log[client_key]
            if now - ts < window
        ]

        # Check if over limit
        if len(_request_log[client_key]) >= max_requests:
            retry_after = int(window - (now - _request_log[client_key][0]))
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )

        # Record this request
        _request_log[client_key].append(now)

        return await call_next(request)
