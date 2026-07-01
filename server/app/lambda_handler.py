"""
AWS Lambda entry point (Mangum adapter).

This wraps the EXACT same FastAPI `app` used for local development, so every
cross-cutting concern is preserved with zero duplication:
  • Authentication (JWT dependencies)        • Middleware (CORS, rate limit, error, metering)
  • Dependency injection (Depends(...))       • All routers / routes
  • OpenAPI (/docs, /redoc, /openapi.json served through API Gateway)

Runtime selection:
  • Local / server : `uvicorn app.main:app`            → app/main.py (workers run)
  • AWS Lambda     : handler = `app.lambda_handler.handler`  (workers disabled)

`lifespan="auto"` lets Mangum run FastAPI's startup/shutdown (the connectivity
ping in init_db) once per cold container, and degrades gracefully if lifespan
is unavailable. The lifespan in main.py already detects Lambda (IS_LAMBDA) and
skips the long-running background workers.

NOTE: This is preparation only — nothing is deployed. WebSocket (`/ws/...`) and
SSE (`/events/stream`) endpoints require API Gateway WebSocket API / a streaming
transport and are NOT served by this HTTP handler. See LAMBDA.md.
"""

from mangum import Mangum
from app.main import app

# `api_gateway_base_path` can be set if the function is mounted under a stage
# path (e.g. "/prod"); left default for a root-mounted HTTP API.
handler = Mangum(app, lifespan="auto")
