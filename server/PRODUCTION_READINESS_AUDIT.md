# TrendZZo — Production Readiness Audit

Read-only audit. **No code was modified.** Findings are grounded in the current
source. Severity legend:

- 🔴 **Blocker** — must fix before/at production (security or correctness at scale)
- 🟠 **High** — fix soon; breaks under multi-instance/scale or weakens security
- 🟡 **Medium** — should address; operational risk or tech debt
- 🟢 **OK / Info** — acceptable or already handled

## Verdict at a glance

| Area | Status | Headline |
|------|--------|----------|
| Authentication | 🟠 | JWT works, but WebSocket endpoint is unauthenticated; tokens are non-revocable for 30 days |
| MongoDB | 🟢 | Tuned pool + indexes; only retention/cleanup follow-ups |
| Middleware | 🟠 | Rate limiter is in-memory per-process (ineffective at scale) |
| Scheduler | 🟠 | Per-process; multi-instance would double-process due posts |
| AI | 🟡 | Shared client + usage tracking; no explicit call timeouts |
| Uploads | 🟠 | Works via Cloudinary; proxy body-size + dead `file_storage.py` |
| Campaigns | 🟢/🟡 | Solid; long AI jobs run synchronously (timeout risk) |
| Publishing | 🟢/🟡 | Solid state machine; double-post risk tied to scheduler scaling |
| Notifications | 🟠 | DB-backed, but SSE/WS fan-out is in-memory per-process |
| WebSockets | 🔴 | Unauthenticated + in-memory + not horizontally scalable |
| Configuration | 🟠 | No fail-fast validation; insecure silent defaults possible |
| Environment Variables | 🟡 | `.env` safe; LinkedIn redirect still placeholder |

**Overall:** Functional and acceptable for a **single instance**. **Not ready for
horizontal scaling or serverless** as-is, due to in-memory state (rate limiter,
scheduler, WS, SSE), an unauthenticated WebSocket, and missing config fail-fast.

---

## 1. Authentication 🟠
**Validated:** HS256 JWT via `utils/jwt_handler.py`; `get_current_user` /
`require_role` dependencies used consistently across routers; login/signup/OTP
flow present; frontend sets the auth header synchronously (race fixed earlier).

**Findings:**
- 🔴 **WebSocket `/ws/{user_id}` is unauthenticated** — `user_id` is taken from
  the path with no token check. Any client can subscribe as any user. (The admin
  WS at `/super-admin/ws` *does* validate a token; the main one does not.)
- 🟠 **Tokens are stateless and long-lived (30 days).** `get_current_user` never
  consults the DB, so a **suspended user or a role change does not take effect
  until token expiry**. No refresh tokens, no revocation list.
- 🟡 **`/generate` accepts anonymous calls** — it decodes the token optionally and
  swallows errors; moderation/flagging only runs when a `user_id` is present.
- 🟡 Rate-limit client key uses the **last 8 chars of the token** — weak/collidable.

## 2. MongoDB 🟢
**Validated:** Single shared Motor client (`database.py`) with tuned pool
(`maxPoolSize`, idle recycling, retries, zlib compression); hot-path indexes
created concurrently and resiliently; `/health` pings the DB.

**Findings:**
- 🟡 **No TTL/retention** on `metering_events` and `generation_logs` — unbounded
  growth. Add TTL indexes (windows are a business decision).
- 🟡 Index creation runs every startup (idempotent; skipped on Lambda already).
- 🟢 Dead `notifications (user_id, read)` index noted in the DB optimization doc —
  drop when convenient.

## 3. Middleware 🟠
**Validated:** CORS (credentials auto-disabled only when origins = `*`), error
handler, rate limiter, metering — ordering documented in `main.py`.

**Findings:**
- 🟠 **Rate limiter is an in-memory sliding window** (`middleware/rate_limiter.py`).
  It does not share state across workers/instances and resets on every deploy, so
  real limits are `configured × instance_count` and reset frequently. Needs Redis
  (or API Gateway throttling) for meaningful protection at scale.
- 🟡 **No unified error schema.** `ErrorHandlerMiddleware` catches unhandled
  exceptions, but FastAPI `HTTPException` / `RequestValidationError` use Starlette
  defaults — response shapes differ across error types.
- 🟢 Metering middleware is fail-safe and non-blocking (verified earlier).

## 4. Scheduler 🟠
**Validated:** Event-driven refactor — `PollingScheduler → trigger →
PostPublishingService` with clean separation; stuck-post cleanup; backward-compat
public API.

**Findings:**
- 🟠 **Per-process, no distributed lock.** Running >1 instance means **every**
  instance polls and could dispatch the **same due post** → duplicate publishing.
  Run a single scheduler instance, add a distributed lock, or move to **EventBridge
  Scheduler** (already prepared in `services/publishing/`).
- 🟡 In-memory queue trigger loses queued items on restart (acceptable; posts stay
  `scheduled` and are re-discovered).
- 🟢 Legacy `scheduled_posts` path retained but marked deprecated.

## 5. AI 🟡
**Validated:** Single shared OpenRouter client (`core/ai_client.py`), per-platform
generation, moderation pre-check, usage logging (tokens/cost/model/org/campaign),
mock mode for zero-credit dev.

**Findings:**
- 🟡 **No explicit timeout/retry** on the OpenAI client calls — a slow model holds
  the request open (and, behind API Gateway, risks a 29s timeout).
- 🟡 `generation_time_ms // 7` per-platform attribution is a cosmetic approximation.
- 🟢 Cost table is static (`ai_usage_service.AVAILABLE_MODELS`) — fine, but drifts
  if provider pricing changes.

## 6. Uploads 🟠
**Validated:** `upload_routes.py` uses Cloudinary when configured, local-disk
fallback otherwise; 10 MB cap; content-type allow-list; auth required.

**Findings:**
- 🟠 **Proxy body size** must be ≥ the app limit or uploads 413 (we hit this on
  EC2/Nginx). Enforce `client_max_body_size 15M;` (Nginx) / ALB equivalent.
- 🟡 **Local fallback writes to ephemeral disk** — lost on redeploy and unusable on
  Lambda. Production **must** set `CLOUDINARY_*`.
- 🟡 **Dead code:** `services/file_storage.py` (S3/Cloudinary/Supabase stubs) is
  **not imported anywhere** — the real path is `upload_routes.py`. Remove to avoid
  confusion.
- 🟢 No malware/content scanning (acceptable for current scope).

## 7. Campaigns 🟢/🟡
**Validated:** Full CRUD with `user_id` ownership scoping; AI strategy, day
content, chat, memory, analytics; unique indexes on memory/analytics.

**Findings:**
- 🟡 **Long-running synchronous AI jobs** (full-campaign strategy + day generation)
  run inside the request — risk of gateway/client timeouts for large campaigns.
  Consider a background job + status polling.

## 8. Publishing 🟢/🟡
**Validated:** `publish_now`, `schedule`, manual-assisted flow, `retry`, status
state machine, event/notification side effects; caller-agnostic `publish_post`.

**Findings:**
- 🟡 **Double-publish risk** under multiple scheduler instances (see §4) — the
  status guard (`posting`/terminal states) mitigates but isn't a hard lock.
- 🟢 Only LinkedIn is auto-publish; others are manual-assisted (by design).

## 9. Notifications 🟠
**Validated:** Notifications persisted in MongoDB; delivered via SSE
(`event_bus`) and WS; unread count + read/read-all endpoints.

**Findings:**
- 🟠 **SSE and WS fan-out are in-memory per-process** — a notification emitted on
  instance A is not delivered to SSE/WS clients on instance B. Multi-instance
  realtime is broken (see `WEBSOCKET_ARCHITECTURE.md`).
- 🟡 **SSE requires a Bearer header** (`Depends(get_current_user)`), but the browser
  `EventSource` API can't send headers, so the frontend falls back to polling —
  an inconsistency worth resolving.
- 🟢 Email side effects are wrapped in try/except so SMTP failures don't break
  publishing (but are awaited inline — a slow SMTP adds latency).

## 10. WebSockets 🔴
**Validated:** `ws/manager.py` connection manager (per-user, channels, broadcast);
`/ws/{user_id}` endpoint; admin WS.

**Findings:**
- 🔴 Unauthenticated main endpoint (see §1).
- 🟠 In-memory state → not horizontally scalable; Lambda-incompatible.
- Full analysis and migration plan already documented in
  `app/WEBSOCKET_ARCHITECTURE.md`.

## 11. Configuration 🟠
**Validated:** Centralized `config.py`; docs disabled in production; CORS
credential rule correct; JWT settings now single-sourced (unified earlier).

**Findings:**
- 🟠 **No startup validation / fail-fast.** `config.py` uses raw `os.getenv` with
  **insecure defaults** (`JWT_SECRET="change-me-in-production"`, `CORS_ORIGINS="*"`).
  In production these should hard-fail if unset, not silently fall back. (Its
  docstring claims Pydantic `BaseSettings` but it doesn't use it.)
- 🟡 Empty `OPENROUTER_API_KEY` silently disables real AI (`/health` shows
  `not_configured`) rather than erroring — easy to miss.

## 12. Environment Variables 🟡
**Validated:** `.env` is gitignored; `.env.example` present and current (Cloudinary
added); pool-tuning vars documented.

**Findings:**
- 🟡 `LINKEDIN_REDIRECT_URI` in `.env` is still the placeholder
  `https://<your-aws-domain>/...` — LinkedIn OAuth will fail until set to the real
  domain (and matched in the LinkedIn app).
- 🟡 `TWITTER_*` / `REDDIT_*` / `MEDIUM_*` are empty — those integrations are
  inert; ensure the UI hides them or shows "coming soon".
- 🟢 Frontend `VITE_*` are build-time — a value change requires a frontend rebuild.

---

## Prioritized remediation

### Before production (Blockers / High)
1. 🔴 **Authenticate the WebSocket handshake** — derive `user_id` from a verified
   JWT, not the path param.
2. 🟠 **Decide the scaling story now:**
   - If **single instance**: document and enforce one app instance + one scheduler.
   - If **multi-instance**: move rate limiting to Redis, add a scheduler
     distributed-lock (or EventBridge), and a Redis/managed fan-out for WS+SSE.
3. 🟠 **Config fail-fast:** in production, refuse to boot if `JWT_SECRET` is the
   default, `CORS_ORIGINS` is `*`, or required secrets are missing.
4. 🟠 **Token revocation/expiry:** shorten access-token lifetime + add refresh, or
   add a lightweight DB/role check on sensitive routes so suspensions take effect.
5. 🟠 **Uploads:** ensure `CLOUDINARY_*` set in prod and proxy body size ≥ 15 MB.

### Soon (Medium)
6. AI client timeouts + retry budget; consider background jobs for long campaign
   generation.
7. TTL retention on `metering_events` / `generation_logs`.
8. Unified API error schema across `HTTPException` / validation / 500.
9. Remove dead `services/file_storage.py`.
10. Set `LINKEDIN_REDIRECT_URI`; hide unconfigured social platforms in the UI.

### Cleanup (Low / Info)
11. Drop the dead `notifications (user_id, read)` index.
12. Stronger rate-limit client key than "last 8 chars of token".
13. Reconcile SSE auth (header vs. EventSource) with the frontend.

---

## What is already solid
- Single shared, well-tuned MongoDB client + comprehensive indexes.
- Fail-safe, non-blocking metering; clean event-driven publishing separation.
- Shared AI client (no duplication); structured usage tracking by org/user/campaign.
- Health endpoint with dependency checks; docs disabled in prod; correct CORS
  credential handling; frontend resilience layer (retry/health/offline) in place.
- Lambda compatibility prepared without breaking local dev.
