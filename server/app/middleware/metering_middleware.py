"""
Global Resource Metering Middleware for TrendZZo.

Captures a metering record for every incoming HTTP request and hands it to the
metering service via a non-blocking enqueue. The request path itself does only
cheap, synchronous work (timing + header reads); the DB write is fully offloaded
to a background worker.

GUARANTEES
----------
1. Fail-safe: any error in metering is caught and swallowed. A metering failure
   can NEVER turn a successful request into a failed one.
2. Near-zero impact: no body buffering, no awaited DB writes on the hot path.
3. Non-blocking: persistence is offloaded to `metering_service` background worker.
4. Business logic untouched: this middleware only observes; it never alters the
   request or the response.

CAPTURED FIELDS
---------------
organization_id, user_id, request_id, endpoint, method, timestamp,
request_bytes, response_bytes, execution_time_ms, status_code,
ai_total_tokens, ai_model, ai_estimated_cost_usd, upload_bytes, download_bytes.
"""

import time
from datetime import datetime, timezone
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

from app.utils import metering_utils as mu
from app.services.metering_service import enqueue

# Paths we never meter (health probes, docs, websockets, the metering API itself)
_SKIP_PREFIXES = (
    "/health", "/", "/docs", "/redoc", "/openapi.json",
    "/system/stats", "/metering",
)
_SKIP_EXACT = {"/", "/health", "/favicon.ico"}


def _should_skip(path: str) -> bool:
    if path in _SKIP_EXACT:
        return True
    # Skip docs + the metering read API to avoid self-metering noise
    return path.startswith(("/docs", "/redoc", "/openapi.json", "/metering"))


class MeteringMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # CORS preflight + skipped paths bypass metering entirely.
        if request.method == "OPTIONS" or _should_skip(request.url.path):
            return await call_next(request)

        # Attach a request id early so handlers/logs can reference it.
        request_id = mu.new_request_id()
        try:
            request.state.request_id = request_id
        except Exception:
            pass

        start = time.perf_counter()
        status_code = 500
        response = None
        try:
            response = await call_next(request)
            status_code = getattr(response, "status_code", 200)
            return response
        finally:
            # This block runs for BOTH success and error paths. Everything here
            # is wrapped so it can never affect the response that's already set.
            try:
                elapsed_ms = round((time.perf_counter() - start) * 1000, 3)
                identity = mu.extract_identity(request)
                req_bytes = mu.request_payload_size(request)
                resp_bytes = mu.response_payload_size(response) if response is not None else 0
                upload = req_bytes if mu.is_upload(request) else 0
                # Download = bytes served for a file/stream GET on the uploads route
                download = resp_bytes if request.url.path.startswith("/uploads/files") else 0
                ai = mu.read_ai_usage(request)

                record = {
                    "request_id": request_id,
                    "organization_id": identity["organization_id"],
                    "user_id": identity["user_id"],
                    "endpoint": request.url.path,
                    "method": request.method,
                    "status_code": status_code,
                    "timestamp": datetime.now(timezone.utc),
                    "execution_time_ms": elapsed_ms,
                    "request_bytes": req_bytes,
                    "response_bytes": resp_bytes,
                    "upload_bytes": upload,
                    "download_bytes": download,
                    "ai_model": ai.get("model"),
                    "ai_prompt_tokens": ai.get("prompt_tokens", 0),
                    "ai_completion_tokens": ai.get("completion_tokens", 0),
                    "ai_total_tokens": ai.get("total_tokens", 0),
                    "ai_estimated_cost_usd": ai.get("estimated_cost_usd", 0.0),
                    "client_ip": mu.client_ip(request),
                    "user_agent": request.headers.get("user-agent"),
                    "exported": False,
                }
                enqueue(record)
            except Exception:
                # Absolutely never let metering break a request.
                pass
