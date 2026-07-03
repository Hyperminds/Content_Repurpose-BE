"""
Lambda request validation.

Parses + validates the API Gateway proxy event into a small, typed request
model. Rejects malformed input (bad JSON, wrong field types, invalid instance
id) with a ValidationError that the handler turns into a 400.
"""

import base64
import json
import re
from dataclasses import dataclass
from typing import Optional

_INSTANCE_ID_RE = re.compile(r"^i-[0-9a-f]{8,17}$")


class ValidationError(Exception):
    """Raised when an incoming request is malformed or invalid."""


@dataclass(frozen=True)
class WorkspaceRequest:
    """Validated request fields shared by the workspace-control endpoints."""

    instance_id: Optional[str] = None   # optional override; else config default
    reason: Optional[str] = None        # optional free-text (audit/logging)
    force: bool = False                 # shutdown: bypass soft checks


def _http_method(event: dict) -> Optional[str]:
    http = (event or {}).get("requestContext", {}).get("http", {})
    return http.get("method") or (event or {}).get("httpMethod")


def is_preflight(event: dict) -> bool:
    """True for a CORS preflight (OPTIONS) request."""
    return (_http_method(event) or "").upper() == "OPTIONS"


def _raw_body(event: dict) -> str:
    body = (event or {}).get("body")
    if body is None:
        return ""
    if (event or {}).get("isBase64Encoded"):
        try:
            return base64.b64decode(body).decode("utf-8")
        except Exception as e:
            raise ValidationError("Request body is not valid base64") from e
    return body


def parse_event(event: dict, require_body: bool = False) -> WorkspaceRequest:
    """
    Parse + validate the event body into a WorkspaceRequest.

    Args:
        require_body: if True, a JSON object body is mandatory.

    Raises:
        ValidationError: malformed JSON / wrong types / invalid instance id.
    """
    raw = _raw_body(event).strip()

    if not raw:
        if require_body:
            raise ValidationError("A JSON request body is required")
        return WorkspaceRequest()

    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as e:
        raise ValidationError("Request body is not valid JSON") from e

    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")

    instance_id = data.get("instance_id")
    if instance_id is not None:
        if not isinstance(instance_id, str) or not _INSTANCE_ID_RE.match(instance_id):
            raise ValidationError("'instance_id' must be a valid EC2 instance id (i-…)")

    reason = data.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValidationError("'reason' must be a string")

    force = data.get("force", False)
    if not isinstance(force, bool):
        raise ValidationError("'force' must be a boolean")

    return WorkspaceRequest(instance_id=instance_id, reason=reason, force=force)
