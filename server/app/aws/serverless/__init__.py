"""
Workspace-control serverless layer.

Always-on Lambda handlers (behind API Gateway) that can start/stop/query the EC2
workspace instance even when the main FastAPI backend is stopped. They reuse the
shared EC2PowerController — no business-logic duplication.

    wake_handler      → POST /workspace/wake
    shutdown_handler  → POST /workspace/shutdown
    status_handler    → GET  /workspace/status

Deployment artifacts (OpenAPI spec + AWS SAM template with API Gateway, DLQ,
CORS, IAM, CloudWatch) live in `server/infra/`.
"""

from app.aws.serverless.handlers import wake_handler, shutdown_handler, status_handler

__all__ = ["wake_handler", "shutdown_handler", "status_handler"]
