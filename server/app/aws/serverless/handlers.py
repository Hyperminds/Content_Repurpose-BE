"""
Workspace-control Lambda handlers.

Three always-on, serverless entrypoints that front the (possibly stopped) EC2
backend:

    wake_handler      → POST /workspace/wake      → start EC2, return "starting"
    shutdown_handler  → POST /workspace/shutdown  → validate + stop EC2, "stopping"
    status_handler    → GET  /workspace/status    → DescribeInstances

They contain NO business logic of their own — each parses/validates the request,
delegates to the shared EC2PowerController (via the AWS DI composition root), and
formats the response. This is the "external waker" that can start the instance
even when the main FastAPI backend is down.

Configuration (env): INSTANCE_ID (required); AWS_REGION is provided automatically
by the Lambda runtime. Credentials come from the function's IAM role.
"""

import asyncio

from app.aws import get_aws_power_controller
from app.aws.exceptions import (
    AWSError,
    AWSAccessDeniedError,
    AWSConfigurationError,
    AWSThrottlingError,
    AWSTimeoutError,
    InstanceNotFoundError,
    InvalidInstanceStateError,
)
from app.aws.serverless.logging_config import bind_request
from app.aws.serverless.request_validation import ValidationError, is_preflight, parse_event
from app.aws.serverless.responses import cors_preflight, error, success

# Exception → (HTTP status, error code) mapping. Order matters (most specific
# first); every entry is an AWSError subclass except ValidationError.
_ERROR_MAP = [
    (ValidationError, (400, "validation_error")),
    (AWSConfigurationError, (500, "configuration_error")),
    (InstanceNotFoundError, (404, "instance_not_found")),
    (InvalidInstanceStateError, (409, "invalid_instance_state")),
    (AWSAccessDeniedError, (403, "access_denied")),
    (AWSThrottlingError, (429, "throttled")),
    (AWSTimeoutError, (504, "timeout")),
    (AWSError, (502, "aws_error")),
]


def _error_response(exc: Exception, logger) -> dict:
    for exc_type, (status_code, code) in _ERROR_MAP:
        if isinstance(exc, exc_type):
            logger.error("request failed", code=code, error=str(exc))
            return error(str(exc), code, status_code)
    logger.error("unexpected error", error=str(exc), kind=type(exc).__name__)
    return error("Internal server error", "internal_error", 500)


# ── Handlers ──────────────────────────────────────────────────────────────────
def wake_handler(event, context):
    """POST /workspace/wake — start the EC2 instance; return {"status": "starting"}."""
    logger = bind_request(event, context, "wake")
    if is_preflight(event):
        return cors_preflight()
    try:
        req = parse_event(event, require_body=False)
        controller = get_aws_power_controller()
        result = asyncio.run(controller.startup(req.instance_id))
        logger.info("wake requested", instance_id=result.instance_id,
                    state=result.state.value, issued=result.requested)
        return success({
            "status": "starting",
            "instance_id": result.instance_id,
            "state": result.state.value,
        })
    except Exception as e:  # noqa: BLE001 — mapped to a structured error response
        return _error_response(e, logger)


def shutdown_handler(event, context):
    """POST /workspace/shutdown — validate, stop the EC2 instance; return {"status": "stopping"}."""
    logger = bind_request(event, context, "shutdown")
    if is_preflight(event):
        return cors_preflight()
    try:
        # Shutdown validates its request body (optional reason/force/instance_id).
        req = parse_event(event, require_body=False)
        controller = get_aws_power_controller()
        result = asyncio.run(controller.shutdown(req.instance_id))
        logger.info("shutdown requested", instance_id=result.instance_id,
                    state=result.state.value, issued=result.requested,
                    reason=req.reason, force=req.force)
        return success({
            "status": "stopping",
            "instance_id": result.instance_id,
            "state": result.state.value,
        })
    except Exception as e:  # noqa: BLE001
        return _error_response(e, logger)


def status_handler(event, context):
    """GET /workspace/status — report the EC2 instance status."""
    logger = bind_request(event, context, "status")
    if is_preflight(event):
        return cors_preflight()
    try:
        controller = get_aws_power_controller()
        status = asyncio.run(controller.status())
        logger.info("status", instance_id=status.instance_id, state=status.state.value)
        return success({
            "status": status.state.value,
            "label": status.state.label,
            "instance_id": status.instance_id,
        })
    except Exception as e:  # noqa: BLE001
        return _error_response(e, logger)
