# Trendzzo Hybrid Social Publishing Architecture

> **Trendzzo owns orchestration. Providers own execution.**

This document describes how Trendzzo publishes content across multiple platforms
using multiple execution methods (official APIs, external browser agents,
user-assisted flows, and customer-supplied credentials) without coupling the
scheduler or worker to any particular platform or method.

---

## 1. Trendzzo publishing architecture

```
                    TRENDZZO  (control plane)
                       |
                 Publishing Engine  (scheduler + worker)
                       |
                 Provider Router
                       |
        +--------------+---------------+--------------+
        |              |               |              |
      NATIVE        EXTERNAL      USER-ASSISTED      BYOK
      (APIs)        (Hermes)        (Hermes)      (own creds)
        |              |               |              |
        +--------------+---------------+--------------+
                       |
                 ProviderResult (normalized)
                       |
                 Trendzzo Job State
```

**Trendzzo (the control plane) owns:** users, tenants, connected accounts,
content, campaigns, scheduling, publishing jobs, retries, idempotency, job
state, audit logs, permissions, publishing history, status reporting, and
provider selection.

**Providers own ONLY platform execution.** A provider receives a normalized
instruction and returns a normalized result. It never schedules, queues,
retries, resolves tenants, or runs campaign logic.

The core principle is enforced structurally: the worker resolves a provider and
calls `provider.publish(...)`. There is **no** `if platform == ...` branching in
the engine.

---

## 2. Native API providers

Native providers use the official platform APIs and OAuth. They are the
preferred, default method and are always available.

| Platform            | Provider (`provider_name`) | Method       |
| ------------------- | -------------------------- | ------------ |
| LinkedIn            | `linkedin`                 | LinkedIn API |
| Instagram           | `meta`                     | Meta API     |
| Facebook Pages      | `meta`                     | Meta API     |
| Threads             | `meta`                     | Meta/Threads |
| X (Twitter)         | `x`                        | X API        |

`NativeApiProvider` (`providers/native_provider.py`) adapts the **existing**
`PublisherRegistry` + platform publishers to the provider interface. The native
publishing path (LinkedIn/Meta/X) is behaviorally unchanged.

---

## 3. Hermes external provider

`HermesPublisherProvider` (`providers/hermes/provider.py`) executes publishing
through a browser agent (Hermes) for platforms where such automation is
permitted. It:

- owns no scheduling/queue/retry/tenant/campaign logic;
- receives a normalized `PublishInstruction`;
- routes to a per-platform `HermesPlatformWorkflow`;
- drives execution through the single `HermesExecutionAdapter` boundary;
- returns a normalized `ProviderResult`.

The **first real** browser workflow is Reddit user-assisted publishing (see
§17). Alongside it, a `FakeHermesWorkflow` + `FakeHermesExecutionAdapter` remain
for tests and wiring validation. Hermes is disabled by default
(`ENABLE_HERMES_PROVIDER=false`); the native LinkedIn/Meta/X path is unaffected.

---

## 4. User-assisted provider

`USER_ASSISTED_AGENT` is a Hermes provider variant for platforms that legitimately
require manual steps (login, MFA, confirmation, account selection). Instead of
failing the job, the flow pauses:

```
PUBLISHING → ACTION_REQUIRED → WAITING_FOR_USER → PUBLISHING → PUBLISHED
```

The UI shows "Action required to finish publishing" rather than an error.
Gated by `ENABLE_HERMES_USER_ASSISTED=false`.

---

## 5. BYOK providers

BYOK ("Bring Your Own API Credentials") lets a customer supply their own
legitimate platform API credentials (e.g. their own X API app).

```
User → Connect (BYOK) → own dev credentials → encrypted store
     → BYOK provider (provider_type = BYOK_API) → official platform API
```

- Always tenant + account scoped (`ByokCredentialStore`).
- Credentials encrypted at rest (Fernet via `CredentialVault`); never returned
  to the frontend after storage; never logged.
- Uses the platform's **official** API — never a bypass of pricing, limits, or
  policy.
- Only the abstraction (`BaseByokProvider`) + storage contract exist today.
  Gated by `ENABLE_BYOK_PROVIDERS=false`.

---

## 6. Provider Router

`ProviderRouter` (`providers/router.py`) resolves an account to a provider by
its `provider_type` — never by scattered conditionals.

```python
provider = router.resolve(account)   # by account.provider_type
result   = await provider.publish(instruction, access_token)
```

- `NATIVE_API` → the registered native provider
- `EXTERNAL_AGENT` / `USER_ASSISTED_AGENT` → the Hermes provider (if registered)
- `BYOK_API` → a BYOK provider keyed by `provider_name`

If no provider is registered for the required type, `resolve()` returns `None`
and the engine reports `EXTERNAL_PLATFORM_UNSUPPORTED`. **It never silently
falls back to a different execution method.**

---

## 7. Capability system

Every provider exposes `capabilities(platform) -> ProviderCapabilities`:

```json
{
  "text": true, "image": true, "video": true, "links": true,
  "scheduling": true, "analytics": false,
  "browser_automation": false, "user_confirmation": false
}
```

Before execution the engine derives the job's `ContentRequirements` and calls
`unmet_requirements(caps, reqs)`. If anything is unmet, the job fails fast with
a clear reason instead of erroring deep inside a provider.

---

## 8. Authentication

Two supported models:

- **OAUTH** (preferred): the existing OAuth infrastructure. Native + BYOK.
- **BROWSER_SESSION**: for Hermes. The user authenticates in a browser; if MFA
  occurs, the flow pauses and requests user interaction — **MFA is never
  bypassed**.

Trendzzo never stores social-media passwords. OAuth is always preferred over
browser login when a platform supports it.

---

## 9. Session management

When Hermes needs persistent browser state, Trendzzo stores only an
`ExternalSessionReference` — an **encrypted, opaque handle** to session state
that lives in an isolated store owned by the Hermes runtime.

- Raw cookies/session contents are **never** stored in ordinary documents.
- The reference is Fernet-encrypted, tenant + account scoped.
- The domain model returned to callers never includes the encrypted reference.
- Session data is **never** returned to the frontend.

---

## 10. Job lifecycle

```
PENDING → CLAIMED → EXECUTING → COMPLETED
                        │
                        ├── VERIFYING → PUBLISHED / FAILED / UNKNOWN
                        ├── ACTION_REQUIRED → WAITING_FOR_USER → PUBLISHING
                        └── FAILED / RETRYING
```

Native publishing uses the classic states. External/user-assisted providers add
`VERIFYING`, `ACTION_REQUIRED`, `WAITING_FOR_USER`, and `UNKNOWN`.

---

## 11. The UNKNOWN state

If a provider result is uncertain (e.g. Hermes clicked Publish but the
connection dropped), the outcome is `UNKNOWN`.

**UNKNOWN is never blindly retried.** The only allowed transitions are
`UNKNOWN → VERIFYING` or `UNKNOWN → CANCELLED`. The system verifies whether the
post actually exists before deciding PUBLISHED vs FAILED. This prevents
duplicate posts. Enforced two ways:

- `VALID_TRANSITIONS` forbids `UNKNOWN → RETRYING/QUEUED`.
- The job queue's `claim_next` only picks up `PENDING`/`RETRYING`, so UNKNOWN
  jobs are never auto-claimed.

---

## 12. Idempotency

Every operation has a stable `operation_id`:

```
operation_id = "op_" + job_id + "_" + account_id + "_" + content_version
content_version = sha256(content + sorted(media_urls))[:12]
```

A plain retry of unchanged content reuses the same id (idempotent). Editing the
content changes `content_version` and therefore the id (a genuinely new
operation). The provider receives the `operation_id` so it can de-duplicate, and
Trendzzo persists `external_post_id` / `external_url` / `published_at` on
success.

---

## 13. Security

- **Tenant isolation:** the worker loads the account with a tenant-scoped query
  before routing; every repo query is tenant-scoped.
- **Credential storage:** OAuth tokens and BYOK credentials are Fernet-encrypted
  at rest (`CredentialVault`), never returned to the frontend, never logged.
- **Session isolation:** only an encrypted reference is stored; session contents
  live in an isolated store.
- **Filesystem / path traversal:** Hermes media uses a temporary restricted
  workspace; filenames are validated (`is_safe_workspace_name`) and resolved
  strictly inside the workspace (`resolve_within`), blocking `..`, absolute
  paths, separators, and cross-drive escapes.
- **SSRF:** user-supplied media URLs are checked with `url_safety` at the fetch
  boundary (native integrations); the provider layer performs no unguarded
  fetches.
- **Secret leakage:** provider errors and observability events are sanitized —
  messages/keys containing tokens/cookies/passwords/session data are redacted.
  Raw browser/adapter stack traces are never surfaced to users.
- **Retry duplication:** UNKNOWN → verify-first prevents duplicate publishes.

---

## 14. Platform compliance

Automation is **default-deny**. `PlatformAutomationPolicy` is an explicit
allow-list; a platform is automatable only if it has been explicitly allowed
(after a documented compliance review) **and** its workflow declares
`automation_allowed`. Both must agree.

Trendzzo must **never** be used to circumvent platform restrictions. The
following are prohibited and are not implemented anywhere:

- CAPTCHA solving/bypass, MFA bypass, anti-bot/fingerprint/stealth evasion,
  proxy rotation for evasion, rate-limit / API-restriction / payment /
  security-control bypass.

X and Meta keep their official API/OAuth integrations; automation is never a
substitute for API access or pricing.

---

## 15. How to add a new provider

1. Implement the `PublishingProvider` protocol (`providers/base.py`):
   `provider_name`, `provider_type`, `capabilities(platform)`,
   `async publish(instruction, access_token) -> ProviderResult`.
2. Return normalized `ProviderResult` using `ExternalResultStatus` /
   `ExternalErrorCode`. Sanitize error messages.
3. Register it on the `ProviderRouter` in `main.py`, gated behind a feature flag
   if experimental.
4. Ensure accounts using it are created with the matching `provider_type` /
   `provider_name`.
5. Add tests (see `tests/social_publishing/test_hybrid_providers.py`).

---

## 16. How to add a Hermes platform workflow

1. Implement the `HermesPlatformWorkflow` protocol
   (`providers/hermes/workflow.py`): `platform`, `automation_allowed`,
   `requires_user_action`, `capabilities()`,
   `async execute(instruction, adapter)`.
2. Set `automation_allowed = False` until a documented compliance review
   approves the platform. Include **no** bypass logic of any kind.
3. Drive execution only through the `HermesExecutionAdapter`
   (connect → compose → media → publish → verify → close). Never touch a browser
   directly from business logic.
4. Map outcomes to normalized `ProviderResult`s; return `UNKNOWN` (not a failure)
   when the outcome is uncertain so the engine verifies before retrying.
5. Register the workflow on the Hermes provider, and add the platform to the
   `PlatformAutomationPolicy` allow-list only after approval.
6. Add tests using `FakeHermesExecutionAdapter`. Never publish real content in
   automated tests.

---

## 17. Reddit Hermes user-assisted workflow (first real workflow)

Reddit is the **first real Hermes platform workflow**. It is user-assisted: the
system prepares a post in a real browser, then **stops and waits for the user to
explicitly confirm** before the final submit. It never stores Reddit passwords,
never bypasses MFA/CAPTCHA, and never posts autonomously.

### Semantic browser boundary

Real workflows do not talk to Playwright directly. They depend only on the
`SemanticBrowser` protocol (`providers/hermes/semantic_browser.py`) — an
accessibility-first contract (`goto`, `current_url`, `is_visible`, `get_text`,
`wait_for`, `wait_for_url_contains`, `fill`, `click`, `set_input_files`,
`screenshot`), all located by ARIA **role + accessible name** (never pixel
coordinates) and all bounded by a timeout. This sits **alongside** the coarse
`HermesExecutionAdapter`, so existing fake-adapter code and tests are untouched.

`PlaywrightHermesAdapter` (`providers/hermes/playwright_adapter.py`) is the real
implementation. Playwright is imported **lazily inside `start()`**, so the app
and the whole test suite import and run cleanly without Playwright installed;
`start()` raises a clear, actionable `BrowserError` if the runtime is missing.
It uses a **persistent browser context** (an on-disk profile per
tenant+account) so the Reddit login session survives between the prepare and
confirm requests — the user is not asked to sign in twice.

### Two-phase flow

```
prepare()                                  confirm_and_publish()
─────────                                  ─────────────────────
validate (title/subreddit/media)           re-verify subreddit
verify authenticated session               click "Post"   ← only after
  └─ if not: goto /login → ACTION_REQUIRED    explicit user confirmation
goto /r/<sub>/submit                        wait for /comments/ permalink
subreddit safety check                        ├─ found  → PUBLISHED (+url,id)
select Text/Image tab                         └─ timeout→ UNKNOWN (verify, no
fill title / body / attach media                        blind-retry)
wait for "Post" button (DO NOT click)
        → ACTION_REQUIRED
```

- **Phase A `prepare()`** fills everything up to but **not including** the Post
  button, then returns `ACTION_REQUIRED`. Validation failures return `FAILED`
  and never open a browser.
- **Phase B `confirm_and_publish()`** is the only place the Post button is
  clicked, and only after the user confirms. After the click, any uncertainty is
  `UNKNOWN` (never a blind retry) so a post is never duplicated.
- `execute()` (the scheduled-job path) runs Phase A only and returns
  `ACTION_REQUIRED`. A scheduled Reddit job therefore **surfaces "action
  required"** and never publishes silently in the background.

### Service, persistence, and lifecycle

`RedditUserAssistedService` (`providers/hermes/reddit/service.py`) is the glue
the API routes call. Because a browser page's in-memory composer form does not
survive a fresh browser launch, `confirm()` **re-runs `prepare()`** (idempotent,
same `operation_id`) and then submits, all in **one** browser session in one
request; login persists via the on-disk profile.

A prepared post is persisted as a `PendingConfirmation`
(`sp_pending_confirmations`, tenant-scoped, upserted by `operation_id` so it is
idempotent). It stores only the post preview + an opaque profile key — **no
cookies, passwords, or session contents**. Confirmations have a bounded lifetime
(default 900s); a background `ConfirmationExpiryWorker` marks overdue ones
`expired` and **never reschedules or auto-retries** them.

```
prepare → ACTION_REQUIRED ──confirm──▶ WAITING_FOR_USER ──▶ PUBLISHED
                          ├──cancel──▶ CANCELLED               │  │
                          └──expire──▶ EXPIRED                  │  └▶ UNKNOWN
                                                                └───▶ FAILED
```

### API (all tenant-scoped; 404 unless `ENABLE_HERMES_PROVIDER=true`)

| Method | Path                                   | Purpose                        |
| ------ | -------------------------------------- | ------------------------------ |
| POST   | `/social-publishing/reddit/posts`      | Prepare a post → ACTION_REQUIRED |
| POST   | `/social-publishing/reddit/confirm/{id}` | Confirm + publish            |
| POST   | `/social-publishing/reddit/cancel/{id}`  | Cancel without publishing    |
| GET    | `/social-publishing/reddit/pending`    | List awaiting confirmations    |

The frontend surfaces these on the Social Accounts page: Reddit shows
**Provider: Hermes · Method: User-assisted browser**, and pending posts render a
preview with **Publish to Reddit** / **Cancel** buttons.

### Capabilities

`text`, `image`, and `links` are supported; **`video` is `false`** (not reliably
verifiable yet); `browser_automation` and `user_confirmation` are `true`.

### Audit events

Every phase emits a sanitized observability event via `observability.emit`:
`external_publish_started` → `action_required` → `verifying` → `completed`, or
`unknown` / `failed`. Secret-bearing keys are dropped by `_scrub`.

### Enabling it (live)

Off by default. To run the live user-assisted flow, install Playwright and set
the flag — see `scripts/reddit_smoke_test.md` for the exact, step-by-step
manual smoke test. Automated tests use a **scripted fake `SemanticBrowser`** and
never touch a real browser or post real content.

---

## Feature flags

| Flag                          | Default | Effect                                   |
| ----------------------------- | ------- | ---------------------------------------- |
| `ENABLE_HERMES_PROVIDER`      | `false` | Register the Hermes provider + Reddit user-assisted workflow. |
| `ENABLE_HERMES_USER_ASSISTED` | `false` | Enable user-assisted Hermes mode.        |
| `ENABLE_BYOK_PROVIDERS`       | `false` | Enable BYOK providers.                   |

Reddit user-assisted browser tuning (all optional, safe defaults):
`HERMES_HEADLESS` (default headful so the user can complete login/MFA),
`HERMES_BROWSER_PROFILE_DIR` (persistent profile location),
`HERMES_CONFIRMATION_SWEEP_SECONDS` (expiry sweep interval, default 60).

Native API publishing is unaffected by these flags and always available.

## Code map

| Concern              | Location                                              |
| -------------------- | ----------------------------------------------------- |
| Provider protocol    | `app/social_publishing/providers/base.py`             |
| Capabilities         | `app/social_publishing/providers/capabilities.py`     |
| Neutral errors       | `app/social_publishing/providers/errors.py`           |
| Native provider      | `app/social_publishing/providers/native_provider.py`  |
| Router               | `app/social_publishing/providers/router.py`           |
| Operation / idempotency | `app/social_publishing/providers/operation.py`     |
| Observability        | `app/social_publishing/providers/observability.py`    |
| Hermes provider      | `app/social_publishing/providers/hermes/provider.py`  |
| Hermes adapter       | `app/social_publishing/providers/hermes/adapter.py`   |
| Hermes workflow base | `app/social_publishing/providers/hermes/workflow.py`  |
| Automation policy    | `app/social_publishing/providers/hermes/automation_policy.py` |
| Session store        | `app/social_publishing/providers/hermes/session_repository.py` |
| Media workspace      | `app/social_publishing/providers/hermes/media_workspace.py` |
| Semantic browser (real boundary) | `app/social_publishing/providers/hermes/semantic_browser.py` |
| Playwright adapter (real)  | `app/social_publishing/providers/hermes/playwright_adapter.py` |
| Reddit workflow      | `app/social_publishing/providers/hermes/reddit/workflow.py` |
| Reddit validation/constants | `app/social_publishing/providers/hermes/reddit/{validation,constants}.py` |
| Reddit service       | `app/social_publishing/providers/hermes/reddit/service.py` |
| Reddit API routes    | `app/social_publishing/api/reddit_routes.py`          |
| Pending confirmations | `app/social_publishing/providers/hermes/pending_confirmation_repository.py` |
| Confirmation expiry worker | `app/social_publishing/providers/hermes/confirmation_expiry_worker.py` |
| Reddit smoke test    | `scripts/reddit_smoke_test.md`                        |
| BYOK abstraction     | `app/social_publishing/providers/byok/`               |
| Worker wiring        | `app/social_publishing/jobs/worker.py`                |
| Lifecycle states     | `app/social_publishing/domain/enums.py`               |
