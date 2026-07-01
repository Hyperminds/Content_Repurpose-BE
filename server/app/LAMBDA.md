# AWS Lambda Compatibility (Mangum)

Preparation only — **nothing is deployed.** The same FastAPI app runs in two
runtimes from one codebase.

## Entry points

| Runtime | Command / handler | Background workers |
|---------|-------------------|--------------------|
| Local / server | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | **enabled** (scheduler + metering) |
| AWS Lambda | handler = `app.lambda_handler.handler` | **disabled** (managed services instead) |

`app/lambda_handler.py`:
```python
from mangum import Mangum
from app.main import app
handler = Mangum(app, lifespan="auto")
```

## What is preserved (identical in both runtimes)
Because Lambda wraps the **same `app` object**, all of these are unchanged:
- **Authentication** — JWT `get_current_user` / `require_role` dependencies.
- **Middleware** — CORS, rate limiter, error handler, metering (all still run;
  Mangum executes the full ASGI middleware stack per request).
- **Dependencies** — every `Depends(...)` works as-is.
- **Routes** — all 140+ routes mounted exactly as before.
- **OpenAPI** — `/docs`, `/redoc`, `/openapi.json` are served through API Gateway
  (subject to the existing `APP_ENV != "production"` gate for docs).

## What changes under Lambda (and why)
Lambda invocations are short-lived and frozen between requests, so long-running
loops can't run there. Detected automatically via `AWS_LAMBDA_FUNCTION_NAME`
(`config.IS_LAMBDA`); the lifespan in `main.py` then:

1. **Skips the polling scheduler.** Scheduling moves to **Amazon EventBridge
   Scheduler**, which fires the already-prepared publish trigger
   (`app/services/publishing/`) → `PostPublishingService.publish_post(post_id)`.
   The architecture was built for exactly this swap.
2. **Skips the in-process metering queue consumer.** The metering middleware
   still runs; for Lambda, point the metering sink at a durable target
   (direct insert, SQS, or Kinesis Firehose) instead of the in-process queue.
3. **Skips per-cold-start index creation** (`init_db(create_indexes=False)`).
   Run index creation once via a migration job (`init_db()` / `setup_db.py`).
   The connectivity ping still runs so a bad DB connection fails fast.

## Not handled by the HTTP handler
- **WebSocket** (`/ws/{user_id}`) — needs API Gateway **WebSocket API** (separate
  Mangum/route integration), not the HTTP handler.
- **SSE** (`/events/stream`) — long-lived streaming is incompatible with Lambda;
  use WebSocket API or a push service.

## Packaging notes (when you do deploy — not now)
- Add `mangum>=0.17.0` (already in `requirements.txt`).
- Handler string for the Lambda config: `app.lambda_handler.handler`.
- Provide env vars (MONGODB_URL, JWT_SECRET, OPENROUTER_API_KEY, CORS_ORIGINS,
  CLOUDINARY_*, etc.) via Lambda environment / Secrets Manager.
- MongoDB Atlas: reuse the module-level Motor client (already a singleton) so the
  connection pool survives across warm invocations; keep `maxPoolSize` small
  (e.g. 5–10) since each concurrent Lambda has its own pool.
- Front with API Gateway **HTTP API** (payload v2) → Mangum auto-detects the
  event format.
- Run a one-time index migration after deploy.

## Local development is unaffected
`uvicorn app.main:app` behaves exactly as before — scheduler and metering
workers start, indexes are created, full lifespan runs. `mangum` is only
imported by `lambda_handler.py`, so it is not required to run locally.
