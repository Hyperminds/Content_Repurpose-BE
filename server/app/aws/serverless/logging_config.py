"""
Structured logging for the workspace-control Lambdas.

Emits single-line JSON to stdout, which Lambda ships to CloudWatch Logs. Each
log line carries the request context (aws_request_id, method, path) so entries
are correlatable across an invocation.

Kept separate from the app's StructuredLogger so the Lambdas have zero coupling
to the FastAPI runtime/config.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

_CONFIGURED = set()


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        meta = getattr(record, "meta", None)
        if isinstance(meta, dict):
            entry.update(meta)
        return json.dumps(entry)


def _base_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if name not in _CONFIGURED:
        logger.setLevel(logging.INFO)
        # Lambda pre-configures the root logger; attach our own JSON handler and
        # stop propagation so lines aren't double-emitted / reformatted.
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.handlers = [handler]
        logger.propagate = False
        _CONFIGURED.add(name)
    return logger


class BoundLogger:
    """A logger bound to a fixed request context; adds it to every line."""

    def __init__(self, logger: logging.Logger, context: dict) -> None:
        self._logger = logger
        self._context = context

    def info(self, msg: str, **meta) -> None:
        self._logger.info(msg, extra={"meta": {**self._context, **meta}})

    def error(self, msg: str, **meta) -> None:
        self._logger.error(msg, extra={"meta": {**self._context, **meta}})


def bind_request(event: dict, context: Optional[object], action: str) -> BoundLogger:
    """Build a request-scoped logger from the API Gateway event + Lambda context."""
    req_ctx = (event or {}).get("requestContext", {})
    http = req_ctx.get("http", {})  # HTTP API (v2)
    method = http.get("method") or (event or {}).get("httpMethod")  # REST (v1)
    path = http.get("path") or (event or {}).get("path")
    request_id = getattr(context, "aws_request_id", None) or req_ctx.get("requestId")

    return BoundLogger(
        _base_logger(f"aws.lambda.{action}"),
        {"action": action, "request_id": request_id, "method": method, "path": path},
    )
