# Global Resource Metering

Production-grade, fail-safe request metering for TrendZZo. Captures a usage
record for **every** incoming HTTP request and persists it to MongoDB via a
non-blocking background worker. Designed to be billing-provider agnostic so the
data can later be streamed to **OpenMeter**, **Lago**, or any usage-based
billing system without touching the capture path.

---

## Architecture

```
                 ┌─────────────────────────────────────────────┐
   HTTP request  │  MeteringMiddleware (outermost)             │
  ─────────────► │   • assigns request_id                      │
                 │   • times the handler                       │
                 │   • on finish: builds record, enqueue()     │  O(1), non-blocking
                 └───────────────┬─────────────────────────────┘
                                 │ enqueue (in-memory asyncio.Queue)
                                 ▼
                 ┌─────────────────────────────────────────────┐
                 │  metering_service background worker         │
                 │   • drains queue in batches                 │
                 │   • bulk insert_many → MongoDB              │  off the request path
                 └───────────────┬─────────────────────────────┘
                                 ▼
                       MongoDB: metering_events
                                 │
                                 ▼  (future)
                       OpenMeter / Lago exporter
                       reads { exported: false }
```

### Components

| File | Responsibility |
|------|----------------|
| `middleware/metering_middleware.py` | Captures request/response metrics, hands off to the service. Never blocks, never raises. |
| `services/metering_service.py` | Buffered async queue + background flush worker. Batched bulk inserts. Read/analytics API. |
| `models/metering_model.py` | `MeteringRecord` schema + serializers. The canonical billing-agnostic event shape. |
| `utils/metering_utils.py` | Fail-safe helpers: identity extraction, size measurement, AI-usage attach/read. |
| `routes/metering_routes.py` | Read-only reporting endpoints (`/metering/*`). |

---

## Captured Fields

| Field | Source |
|-------|--------|
| `request_id` | Generated UUID per request (also set on `request.state.request_id`) |
| `organization_id` | JWT claim `organization_id` / `org_id`, falls back to `user_id` then `"default"` |
| `user_id` | JWT claim `user_id` (None for anonymous) |
| `endpoint` | `request.url.path` |
| `method` | HTTP method |
| `status_code` | Final response status (post error-handling) |
| `timestamp` | UTC capture time |
| `execution_time_ms` | Wall-clock handler time (`time.perf_counter`) |
| `request_bytes` | Inbound `Content-Length` |
| `response_bytes` | Response body length / `Content-Length` |
| `upload_bytes` | `request_bytes` when `Content-Type: multipart/form-data` |
| `download_bytes` | `response_bytes` when serving `/uploads/files/*` |
| `ai_model` | Attached by handlers via `record_ai_usage()` |
| `ai_prompt_tokens` / `ai_completion_tokens` / `ai_total_tokens` | Attached by handlers |
| `ai_estimated_cost_usd` | Attached by handlers |
| `client_ip` | `X-Forwarded-For` (proxy-aware) or socket peer |
| `user_agent` | Request header |
| `exported` | `false` until shipped to an external billing provider |

---

## Guarantees

1. **Fail-safe** — the entire capture runs inside a `try/finally` with a broad
   `except`. A metering bug, a full queue, or a Mongo outage can never convert a
   successful request into a failure.
2. **Near-zero impact** — the request path only does timing + header reads +
   one `put_nowait()`. No body buffering. No awaited DB writes.
3. **Non-blocking** — persistence is fully offloaded to a background worker that
   batches writes (`insert_many`).
4. **Business logic untouched** — the middleware only observes. The single
   AI hook in `content_controller.py` is additive and wrapped in `try/except`.

---

## Capturing AI Usage From a Handler

AI token/cost data lives inside request handlers, so handlers opt in by calling
`record_ai_usage()`. The middleware reads it after the handler returns.

```python
from app.utils.metering_utils import record_ai_usage

# inside any route/controller that consumes AI, with the Request object:
record_ai_usage(
    request,
    model="openai/gpt-4o-mini",
    prompt_tokens=1800,
    completion_tokens=1040,
    estimated_cost_usd=0.0031,
)
```

This is already wired into `POST /generate`. To meter other AI endpoints
(campaign generation, social-presence analysis, trends), add the same one-liner
where those services compute their token usage.

---

## Reporting API

All under `/metering` (excluded from self-metering):

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/metering/summary` | user | Aggregate usage for the caller's organization |
| GET | `/metering/me` | user | Aggregate usage for the current user only |
| GET | `/metering/endpoints?limit=20` | user | Per-endpoint breakdown for the organization |
| GET | `/metering/events?limit=50` | super_admin | Recent raw metering events |
| GET | `/metering/worker` | super_admin | Background worker health (queued / dropped / running) |

---

## MongoDB

Collection: **`metering_events`**

Indexes (created in `database.py → init_db`):
- `organization_id`
- `(organization_id, timestamp desc)`
- `(organization_id, endpoint)`
- `(user_id, timestamp desc)`
- `request_id`
- `(exported, timestamp)` — for the future exporter to find un-shipped records fast

### Retention
`metering_events` grows unbounded. For production, add a TTL index to auto-expire
raw events after, say, 90 days (aggregates/billing snapshots should be persisted
separately before expiry):

```python
await db["metering_events"].create_index("timestamp", expireAfterSeconds=60*60*24*90)
```

---

## Future: OpenMeter / Lago Integration

The schema is already provider-agnostic and every record carries
`exported: false`. A separate exporter (cron or background task) can:

1. Query `metering_events.find({ "exported": false })` in batches.
2. Map each record to the provider's event shape:
   - **OpenMeter** → CloudEvents (`subject = organization_id`,
     `type = "request"` / `"ai.usage"`, `data = { tokens, cost, bytes }`).
   - **Lago** → events API (`external_subscription_id = organization_id`,
     `code = endpoint or "ai_tokens"`, `properties = {...}`).
3. On success, bulk-update those records to `exported: true`.

Because capture, storage, and export are decoupled, switching or adding a
provider requires only a new exporter — no changes to the middleware or handlers.

---

## Tunables (`metering_service.py`)

| Constant | Default | Meaning |
|----------|---------|---------|
| `_MAX_QUEUE` | 10,000 | Hard cap; records dropped beyond this (back-pressure safety) |
| `_BATCH_SIZE` | 200 | Max records per bulk insert |
| `_FLUSH_INTERVAL` | 2.0s | Forced flush cadence when batch isn't full |

Dropped-record count is exposed at `GET /metering/worker` for monitoring.

---

## Tested behaviour

- Captures success **and** error responses (401/422/500) via the `finally` block.
- Captures request/response byte sizes from headers/body.
- Worker starts in app lifespan and flushes remaining records on shutdown.
- No measurable change to handler latency (write is off-path).
