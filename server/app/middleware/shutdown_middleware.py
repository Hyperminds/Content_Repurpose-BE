"""
ShutdownMiddleware — graceful HTTP admission control for complete shutdown.

Two jobs:
  1. Once the ShutdownGate's HTTP admission is closed (step 1 of the shutdown
     sequence), reject NEW requests with 503 so nothing new starts.
  2. While admission is open, count in-flight requests so the orchestrator can
     wait for them to finish (step 2: drain).

Near-zero overhead when not shutting down (an int inc/dec around the request).
CORS preflight (OPTIONS) is always allowed through so the browser can complete
preflight even during shutdown.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from fastapi import Request

from app.workspace.shutdown.state import shutdown_gate


class ShutdownMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        # Admission closed → refuse new work immediately.
        if not shutdown_gate.accepting_http:
            return JSONResponse(
                status_code=503,
                content={"detail": "Server is shutting down", "code": "shutting_down"},
                headers={"Retry-After": "120", "Connection": "close"},
            )

        # Count this request so the drain step knows when everything is done.
        shutdown_gate.inc()
        try:
            return await call_next(request)
        finally:
            shutdown_gate.dec()
