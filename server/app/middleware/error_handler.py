"""
Centralized error handling middleware for TrendZo.
Catches unhandled exceptions and returns structured JSON error responses.
"""

import traceback
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import IS_PRODUCTION


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    Global exception handler.
    - In production: returns clean error messages, logs full traceback
    - In development: returns full error details for debugging
    """

    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            # Log the full traceback
            tb = traceback.format_exc()
            print(f"\n{'='*50}")
            print(f"[ERROR] {request.method} {request.url.path}")
            print(f"Type: {type(exc).__name__}")
            print(f"Message: {str(exc)}")
            if not IS_PRODUCTION:
                print(tb)
            print(f"{'='*50}\n")

            # Determine status code
            status_code = getattr(exc, "status_code", 500)

            # Build response
            error_body = {
                "error": True,
                "message": str(exc) if not IS_PRODUCTION else "Internal server error",
                "type": type(exc).__name__,
                "path": request.url.path,
            }

            # Include traceback in dev mode only
            if not IS_PRODUCTION:
                error_body["traceback"] = tb.split("\n")[-5:]

            return JSONResponse(
                status_code=status_code,
                content=error_body,
            )
