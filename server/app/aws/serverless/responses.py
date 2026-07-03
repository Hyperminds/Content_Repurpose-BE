"""
Lambda response models — API Gateway (proxy) response builders + CORS.

Produces the `{statusCode, headers, body}` shape API Gateway proxy integrations
expect, with a consistent JSON envelope and CORS headers on every response
(including preflight and errors).
"""

import json
from typing import Optional

from app.aws.config.environment import OsEnvironmentProvider

_env = OsEnvironmentProvider()

# Allowed origin is configurable; default to the app's frontend if provided.
_ALLOW_ORIGIN = _env.get("CORS_ALLOW_ORIGIN") or _env.get("FRONTEND_URL") or "*"
_ALLOW_METHODS = "GET,POST,OPTIONS"
_ALLOW_HEADERS = "Content-Type,Authorization,X-Requested-With"


def cors_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": _ALLOW_ORIGIN,
        "Access-Control-Allow-Methods": _ALLOW_METHODS,
        "Access-Control-Allow-Headers": _ALLOW_HEADERS,
        "Access-Control-Max-Age": "600",
    }


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": cors_headers(),
        "body": json.dumps(body),
    }


def success(body: dict, status_code: int = 200) -> dict:
    """A successful JSON response."""
    return _response(status_code, body)


def error(message: str, code: str, status_code: int = 500) -> dict:
    """A structured error response: {error, code, message}."""
    return _response(status_code, {"error": True, "code": code, "message": message})


def cors_preflight() -> dict:
    """Empty 204 response for an OPTIONS preflight request."""
    return {"statusCode": 204, "headers": cors_headers(), "body": ""}
