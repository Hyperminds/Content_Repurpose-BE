"""
Metering utility helpers.

Every function here is FAIL-SAFE: it must never raise into the request path.
On any error it returns a safe default so metering can never break an API call.
"""

import uuid
from typing import Optional
from fastapi import Request
from starlette.responses import Response


# ── Identity helpers ──────────────────────────────────────────────────────────

def new_request_id() -> str:
    """Generate a unique request id."""
    return uuid.uuid4().hex


def extract_identity(request: Request) -> dict:
    """
    Pull org_id / user_id from the JWT in the Authorization header WITHOUT
    raising. Returns {"organization_id", "user_id"} with safe defaults.

    This does NOT enforce auth — it only reads claims if a valid token exists.
    """
    org_id = "default"
    user_id = None
    try:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            from app.utils.jwt_handler import decode_access_token
            payload = decode_access_token(auth.split(" ", 1)[1])
            user_id = payload.get("user_id")
            # org may not exist yet in the token — degrade gracefully
            org_id = payload.get("organization_id") or payload.get("org_id") or user_id or "default"
    except Exception:
        # Invalid/expired token, anonymous request, etc. — never fail metering.
        pass
    return {"organization_id": org_id, "user_id": user_id}


# ── Size helpers ──────────────────────────────────────────────────────────────

def safe_content_length(headers, default: int = 0) -> int:
    """Read a Content-Length header value safely."""
    try:
        val = headers.get("content-length")
        return int(val) if val is not None else default
    except Exception:
        return default


def request_payload_size(request: Request) -> int:
    """
    Best-effort inbound payload size from Content-Length.
    Avoids consuming the request body (which would break downstream handlers).
    """
    return safe_content_length(request.headers, 0)


def response_payload_size(response: Response) -> int:
    """
    Best-effort outbound payload size.
    Prefers an accurate body length, falls back to Content-Length header.
    """
    try:
        body = getattr(response, "body", None)
        if body is not None:
            return len(body)
    except Exception:
        pass
    return safe_content_length(getattr(response, "headers", {}), 0)


def is_upload(request: Request) -> bool:
    """True if the request is a multipart/file upload."""
    try:
        ctype = request.headers.get("content-type", "")
        return ctype.startswith("multipart/form-data")
    except Exception:
        return False


def client_ip(request: Request) -> Optional[str]:
    """Resolve client IP, honouring X-Forwarded-For behind a proxy/Nginx."""
    try:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else None
    except Exception:
        return None


# ── AI usage attachment ───────────────────────────────────────────────────────

def record_ai_usage(
    request: Request,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
) -> None:
    """
    OPTIONAL hook for route handlers/services.

    Call this from inside any handler that consumes AI so the metering
    middleware can attach token + cost data to that request's metering record.
    It writes to request.state and never raises.

    Example (inside a handler):
        from app.utils.metering_utils import record_ai_usage
        record_ai_usage(request, model, prompt_tokens, completion_tokens, cost)

    This is additive — it does not change any existing business logic.
    """
    try:
        request.state.metering_ai = {
            "model": model,
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": int(prompt_tokens or 0) + int(completion_tokens or 0),
            "estimated_cost_usd": float(estimated_cost_usd or 0.0),
        }
    except Exception:
        pass


def read_ai_usage(request: Request) -> dict:
    """Read AI usage attached via record_ai_usage(); empty dict if none."""
    try:
        return getattr(request.state, "metering_ai", {}) or {}
    except Exception:
        return {}
