# FastAPI Project Analysis & Refactor

Constraints honoured: **all API contracts identical, no business logic modified.**
Only cross-cutting infrastructure and duplication were touched. Larger structural
moves are documented as recommendations rather than applied blindly, because
moving modules would churn imports and risk contracts.

---

## Current structure (observed)

```
app/
  main.py                # app factory, middleware stack, router registration, lifespan
  config.py              # centralized settings (env, CORS, JWT, AI, SMTP, OAuth)
  database.py            # single Motor client + indexes (infra)
  core/                  # NEW — shared infra (this refactor)
  middleware/            # error_handler, rate_limiter, metering
  routes/                # 20 routers (HTTP layer)
  controllers/           # request-shaping for some domains (auth, publishing, …)
  services/              # business logic + data access (mixed "repository" + domain)
  models/                # pydantic + mongo doc schemas
  utils/                 # jwt_handler, otp_handler, metering_utils
  ws/                    # websocket manager
  mock_data/             # dev fixtures
```

The layering (routes → controllers/services → models/db) is sound. The main
issues were **duplicated infrastructure wiring** and a few **single-source-of-truth**
violations, not the overall shape.

---

## Applied in this refactor

### 1. Shared AI client (removed 8× duplication)
**Before:** eight services each built their own
`AsyncOpenAI(api_key=os.getenv("OPENROUTER_API_KEY"), base_url="https://openrouter.ai/api/v1")`.
**After:** `app/core/ai_client.py` exposes a single shared `ai_client`, configured
from `config.OPENROUTER_API_KEY` / `config.AI_BASE_URL`. Each service now does
`from app.core.ai_client import ai_client as client`. All `client.chat.completions.create(...)`
calls, prompts, and models are unchanged.

Verified: all 8 services resolve to **one** client instance.

Services updated: `content_service`, `trend_service`, `social_presence_service`,
`campaign_ai_service`, `campaign_chat_service`, `campaign_content_service`,
`campaign_analytics_service`, `campaign_memory_service`.

### 2. Single source of truth for JWT config
**Before:** `utils/jwt_handler.py` re-loaded `.env` and read `JWT_SECRET` /
`JWT_ALGORITHM` / `JWT_EXPIRY_MINUTES` with **different defaults** than
`config.py` (`"fallback_secret_key"` vs `"change-me-in-production"`, `1440` vs
`43200`). In any real environment `.env` sets these, so runtime behaviour was
already identical — but the divergent defaults were a latent bug.
**After:** `jwt_handler` imports the three settings from `config.py`. One source,
no behaviour change with `.env` present.

### 3. Shared identity helper (removed duplicate `_org_of`)
`metering_routes.py` and `ai_usage_routes.py` had an identical private
`_org_of(user)`. Extracted to `app/core/identity.py::org_id_from_user`; both
routes import it. Endpoint behaviour unchanged.

### 4. (Earlier in this effort) infra consolidation already landed
- DB connection pooling + indexes centralized in `database.py`.
- Metering middleware/service/utils packaged under clear modules.
- Publishing split into `services/publishing/` (scheduler / trigger / service).

---

## Recommendations (NOT applied — would touch contracts or many imports)

### Dependency Injection
- **Promote optional-auth to a dependency.** `content_controller.generate_content`
  manually parses the `Authorization` header and decodes the token (duplicating
  `metering_utils.extract_identity`). A FastAPI dependency `get_optional_user`
  in `core/security.py` would remove the duplication. Not applied because it
  touches a controller and changes how the handler receives identity.
- **Inject services instead of importing singletons.** Routes/controllers import
  service singletons directly. Wrapping the AI client and repositories behind
  `Depends(...)` providers would make testing/mocking trivial. This is a
  cross-cutting change to many signatures — defer to a dedicated PR.

### Shared Services / Utilities
- **Consolidate `load_dotenv(... / ".env")`.** ~10 services still call
  `load_dotenv` at import. `config.py` already loads it once; these are now
  redundant. Safe to delete the per-service calls, but it touches many files —
  do it as a focused cleanup PR with full test runs.
- **Unused `from openai import AsyncOpenAI`** imports remain in the 8 refactored
  services (harmless). Remove in a lint-only pass.
- **One serializer per collection.** Serialization helpers (`serialize_*`) are
  duplicated in shape across services; a small `core/serialization.py` could host
  shared converters (e.g. `oid_to_str`, ISO date formatting). Repo-level change.

### Middleware / Error Handling
- **Centralize exception handlers.** Today `ErrorHandlerMiddleware` catches
  unhandled exceptions and returns a structured body. Adding FastAPI
  `@app.exception_handler(RequestValidationError)` / `HTTPException` handlers
  would standardize 4xx bodies — but that **changes error response shape**, so it
  was intentionally NOT done (contract preservation). Plan it with the frontend.
- **Middleware ordering is documented** in `main.py`; keep metering outermost.

### Configuration
- **Adopt pydantic `BaseSettings`.** `config.py`'s module docstring already says
  it "uses Pydantic BaseSettings" but it actually uses raw `os.getenv`. Migrating
  to a typed `Settings` model gives validation + typing with an identical public
  surface (same constant names). Low-risk follow-up.

### Folder structure (target)
```
app/
  core/        # config, security, ai_client, identity, serialization, exceptions
  api/         # routers (rename of routes/) grouped by domain
  domain/      # business services (rename of services/), no infra
  repositories/# data access split out of services
  schemas/     # pydantic request/response (split from mongo doc models)
  infra/       # database, ws, middleware
```
This is the end-state; reaching it means moving files and updating imports, which
should be done incrementally with tests so contracts stay frozen.

---

## Summary
- Removed the highest-impact duplication (8× AI client, 2× org helper) and a
  config divergence (JWT), all verified contract-safe.
- Introduced `app/core/` as the home for shared infrastructure.
- Documented the remaining DI / structure / error-handling improvements that
  require broader edits, so they can be sequenced without risking the API surface.
