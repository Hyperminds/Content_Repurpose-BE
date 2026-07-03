# Workspace + AWS Integration — Production Readiness Review

**Scope:** Workspace Launcher (FE), AWS Integration layer (`app/aws`), Lambda
control plane (`app/aws/serverless`), API Gateway (SAM), PowerController, Shutdown
Orchestrator, Activity Tracking, Health Polling, IAM, CloudWatch, EventBridge.
**Type:** Review + recommendations. **No functionality was modified** — no
currently-reachable critical issue was found (the one latent critical bug is
behind a disabled feature flag; see Bugs §B1).

---

## 1. Architecture Report

### Topology
Two planes, two URLs (correct separation):
- **Control plane** — API Gateway → Wake/Shutdown/Status Lambdas → shared
  `EC2PowerController` (boto3). Always-on; can start the instance when the app
  is down. Frontend uses `VITE_WORKSPACE_CONTROL_URL`.
- **App plane** — EC2/FastAPI backend. Serves `/workspace/health` (startup poll
  target) and the app. Frontend uses `VITE_API_BASE_URL`.

### Component verdicts
| Component | Verdict | Notes |
|---|---|---|
| Workspace Launcher (FE) | Strong | Staged UX, retry/timeout/cancel, offline detection + recovery, auto-redirect. |
| AWS interfaces (`app/aws/interfaces`) | Excellent | Segregated (ISP), value types SDK-neutral, no boto3. |
| EC2PowerController (`app/aws/services`) | Strong | boto3 isolated, lazy, `asyncio.to_thread`, domain errors, structured logs. |
| Config + DI (`app/aws/config`, `factory`) | Strong | `os.environ` touched in one place; composition root; validation. |
| Lambda handlers | Strong | Thin; reuse controller; validation + CORS + JSON logs; no logic dup. |
| API Gateway (SAM) | Good | CORS, throttling, access logs — **no authorizer yet** (blocker). |
| Shutdown Orchestrator | Good | Ordered, best-effort, phased — has a gate-reopen gap (§B1). |
| Activity Tracking | Good | Non-blocking; write-amplification risk (§2). |
| Health Polling (FE) | Strong | `fetch`-isolated, tolerates down backend, offline-aware. |
| IAM | Strong | Least privilege; start/stop scoped to instance ARN. |
| CloudWatch / EventBridge | Good | Alarms + SNS + DLQ; scheduler ships DISABLED (correct). |

### SOLID / DI
- **Strategy + DI**: `PowerController` interface + factory selection + injectable
  session/config. Textbook.
- **ISP**: wake/shutdown/status segregated; `PowerController` is a facade.
- **DIP**: config depends on `EnvironmentProvider`, not `os.environ`; callers
  depend on interfaces + `app.aws.exceptions`, never boto3.
- **OCP**: new platforms = new strategies; no caller changes.

### Code duplication (action item)
- **Two EC2 implementations exist**: `app/workspace/power/ec2.py` (earlier phase)
  and `app/aws/services/ec2_power_controller.py` (AWS module). Both call
  Start/Stop/DescribeInstances. **They can drift.** Recommendation: keep the
  `app/aws` one as canonical and have `app/workspace/power` consume it via a thin
  adapter (in `app/aws/controllers`), or retire the older one. Not urgent
  (workspace power defaults to `local`), but consolidate before AWS go-live.
- **Two `PowerController` abstractions** (`app/workspace/power/base` and
  `app/aws/interfaces/power_controller`) — intentional layering, but document the
  relationship (app-strategy vs infra-contract) and bridge with one adapter.

---

## 2. Optimization Report (Performance / Scalability / Concurrency)

**Performance**
- **Activity write amplification** — one MongoDB upsert per successful request
  (fire-and-forget). Under load this is heavy and all anonymous traffic contends
  on one `"anonymous"` doc. → Throttle (persist only if last write > N s) or
  batch like the metering worker.
- **boto3 client reuse** — cached on the `lru_cache` singleton; reused across warm
  Lambda invocations. Good. `asyncio.to_thread` keeps the loop unblocked. Good.
- **Lambda cold start** — importing `app.aws` pulls `app.services.logger →
  app.config` (dotenv + full settings). Fine, but heavier than needed for a lean
  control-plane function. Optional: a standalone logger to avoid importing
  `app.config`; consider **provisioned concurrency** on the wake Lambda if
  cold-start latency on first wake matters.
- **Health poll** — fixed 5s. Optional exponential backoff after N failures to
  cut request volume during long cold starts.

**Scalability**
- Control plane (Lambda) scales statelessly — good.
- **EC2 app plane is single-instance by design**: `WorkspaceService`,
  lifecycle manager, `shutdown_gate`, `activity_registry`, and sleep signals
  (`ws_manager`, `task_queue`) are all in-process. Behind a multi-instance LB the
  sleep/shutdown decision would be wrong (one instance can't see another's WS
  connections/jobs). → Move shared state to Redis/Mongo before scaling out; until
  then pin to one instance.

**Concurrency**
- `WorkspaceLifecycleManager` serializes ops with an `asyncio.Lock`. ✔
- `activity_registry` uses a `threading.Lock`; `shutdown_gate`/`WorkspaceService`
  rely on single-event-loop semantics (safe under asyncio). ✔
- Lambda containers are isolated; `asyncio.run` per invocation is fine (boto3 is
  sync, no loop binding). ✔
- **Unretained fire-and-forget tasks** — `WakeService.trigger_startup`,
  `ActivityService.track`, `TaskQueue` schedule tasks without holding a
  reference; Python may GC a pending task. → keep a task set + `add_done_callback`.

---

## 3. Security Report

- **CRITICAL — unauthenticated control endpoints.** Both the FastAPI
  `/workspace/*` routes and the API Gateway routes are open. Anyone can start
  (cost), stop, or enumerate state. → **Add a JWT/Lambda authorizer** to the HTTP
  API and auth to the FastAPI routes; rate-limit `wake`. This is the go-live
  blocker.
- **IAM least privilege** — `ec2:Start/StopInstances` scoped to the instance ARN;
  `DescribeInstances` region-conditioned; DLQ send only. ✔ Re-verify the instance
  ARN each deploy.
- **CORS** — defaults to `*`; set to the exact frontend origin in prod.
- **Credentials** — none hardcoded; default provider chain (IAM role). ✔
- **DLQ** — SSE enabled. ✔ Consider a CMK if policy requires.
- **WebSocket endpoint** — unauthenticated (pre-existing); the lifecycle event
  broadcast rides it — ensure no sensitive data is broadcast.
- **Auditability** — enable CloudTrail for Start/Stop actions.

---

## 4. Potential Bugs

- **B1 (critical, currently disabled).** `ShutdownOrchestrator` closes the HTTP
  admission gate at step 1; if a later step raises, the `except` path returns
  **without reopening the gate**, so the instance serves 503 until restart.
  Only reachable when `AUTO_SHUTDOWN_ENABLED=true` (default false), so not a live
  risk today. **Fix before enabling** (and before enabling the EventBridge
  schedule): reopen admission / `shutdown_gate.reset()` on failure, or only close
  the gate once the sequence is committed. Left unmodified per the "don't change
  functionality" guardrail + it needs a small design decision (degraded-state vs
  restart).
- **B2.** Duplicate EC2 controllers may drift (see §1).
- **B3.** Two `/workspace/status` contracts (FastAPI vs Lambda). Frontend handles
  both via `toWorkspaceState`; just don't point the frontend's status call at the
  FastAPI backend and the wake call at API Gateway inconsistently — keep control
  calls on `VITE_WORKSPACE_CONTROL_URL`.
- **B4.** In-memory state resets on restart, and `WorkspaceService` vs
  `WorkspaceLifecycleManager` can diverge (two state holders). Pick one
  authoritative source.
- **B5.** `/workspace/health` (FastAPI) returns 200 as a shallow liveness probe;
  the FE treats 200 as "ready", so it redirects before deep readiness (DB/AI).
  Consider polling the deep `/health` or a readiness endpoint.
- **B6.** Unretained asyncio tasks (see §2 Concurrency).
- **B7.** EventBridge schedule targets the shutdown Lambda, which stops
  **unconditionally**. Must be decision-gated before enabling (schedule ships
  DISABLED, so not live).

---

## 5. Deployment Checklist

- [ ] `boto3` present in `requirements.txt`. ✔
- [ ] `sam validate -t infra/template.yaml` passes.
- [ ] Params: `InstanceId`, `CorsAllowOrigin` (frontend URL), `AlarmEmail`.
- [ ] IAM role instance ARN matches the target instance.
- [ ] Authorizer attached to the HTTP API (§3). **Blocker.**
- [ ] `sam build && sam deploy --capabilities CAPABILITY_NAMED_IAM …`
- [ ] Frontend env: `VITE_WORKSPACE_CONTROL_URL` = API Gateway base;
      `VITE_API_BASE_URL` = EC2 backend.
- [ ] Confirm SNS email subscription.
- [ ] Smoke test: status → wake (stopped→pending) → health → dashboard → shutdown.
- [ ] Force an error → alarm fires → SNS.
- [ ] `EventBridge schedule stays DISABLED` until B1 + B7 resolved.

---

## 6. Production Checklist

**Must-have before go-live**
- [ ] Authentication on all control endpoints (FE routes + API Gateway).
- [ ] Fix B1 (gate reopen) before enabling auto-shutdown / scheduler.
- [ ] Decision-gate the scheduled shutdown (B7).
- [ ] Tighten CORS to the exact origin.
- [ ] Single authoritative workspace state source (B4).

**Reliability / ops**
- [ ] CloudWatch dashboard + composite alarm; alarms wired to on-call.
- [ ] CloudTrail on for EC2 start/stop audit.
- [ ] Runbook: manual wake/stop, DLQ drain, alarm response, rollback (`sam delete`).
- [ ] Load/latency test the wake path (cold start + EC2 boot window).
- [ ] Verify LB/Nginx health-check thresholds don't flap during drain.

**Scale (only if >1 app instance)**
- [ ] Move workspace/lifecycle/gate/activity/sleep-signal state to Redis/Mongo.
- [ ] Otherwise pin the app to a single instance.

**Hygiene**
- [ ] Consolidate the duplicate EC2 controllers (B2) + add the app↔aws adapter.
- [ ] Throttle/batch activity writes (§2).
- [ ] Retain fire-and-forget tasks (B6).

---

## 7. Summary

The stack is **well-architected, SOLID, and largely production-ready**, with a
clean AWS boundary (SDK isolated, DI, segregated interfaces) and a polished,
resilient launcher UX. **Two hard blockers** before production: **(1)
authentication** on the control endpoints and **(2)** resolving the
shutdown-gate reopen (B1) and decision-gating (B7) **before** enabling
auto-shutdown/EventBridge. Everything else (duplication cleanup, activity write
throttling, multi-instance state, deep readiness) is important but non-blocking
for a single-instance launch. No live critical issue was found, so no code was
changed in this review.
