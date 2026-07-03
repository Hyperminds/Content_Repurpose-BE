# Workspace Lifecycle — Architecture Audit

**Scope:** Workspace Launcher, Workspace APIs, Activity Tracking, Sleep/Shutdown
Decision Engine, Shutdown Orchestrator, PowerController, Wake Service, Health
Polling, Lifecycle Manager.
**Type:** Review + recommendations. **No code was modified.**

---

## 1. System Overview

The workspace power system lets a stopped backend (EC2 instance) be woken on
demand and shut down when idle, to save cost. It spans both repos:

```
Frontend (React)                         Backend (FastAPI)
─────────────────                        ─────────────────
WorkspaceLauncher ─▶ WorkspaceContext    /workspace/wake ─▶ WakeService ─▶ PowerController(strategy)
   │                     │                        │                          ├─ LocalPowerController (log)
   │                     ▼                        │                          └─ EC2PowerController (boto3)
   │           WorkspaceStartupService     ShutdownWatcher ─▶ ShutdownDecisionEngine ─▶ SleepDecisionEngine
   │             ├─ HealthPollingService                         │                        └─ SignalCollector
   │             ├─ StartupProgressManager                       ▼                            (ws, tasks, activity, registry)
   │             └─ StartupTimeoutHandler          ShutdownOrchestrator ─▶ ShutdownHooks ─▶ (ws/scheduler/metrics/…)
   │                                                     │
   └─ polls GET /workspace/health                 WorkspaceLifecycleManager ─▶ WorkspaceStateMachine
                                                        └─ LifecycleEventBus ─▶ WebSocket broadcast
```

Design language throughout: **Strategy Pattern** (PowerController), **Dependency
Injection** (factory + `get_*` providers + injectable constructors), **fail-safe
hooks**, and a **single-source-of-truth decision engine** reused by both sleep
and shutdown.

---

## 2. Architecture Review (by component)

| Component | Verdict | Notes |
|---|---|---|
| Workspace Launcher (FE) | Strong | Clean state machine in `WorkspaceContext`; staged UX; cancel/retry/timeout handled. |
| Workspace APIs | Good, **unauthenticated** | Thin routes, DI, clear contracts. See Security §5. |
| Activity Tracking | Good | Non-blocking middleware + repo/service split; index added. Write-amplification risk (§3). |
| Sleep Decision Engine | Strong | Pure validator, configurable policy, fail-safe (blocks on uncertainty), extensible signal registry. |
| Shutdown Orchestrator | Strong | Ordered 11-step sequence, best-effort steps, phase tracking, gate-based admission control. |
| PowerController | Excellent | Textbook Strategy + DI; SDK isolated to `ec2.py`; log-only default; safe factory fallback. |
| Wake Service | Good | Fire-and-forget startup; strategy-agnostic; correct “don’t wait” semantics. |
| Health Polling (FE) | Strong | `fetch`-isolated from axios; tolerates a fully-down backend; retry limit + overall timeout + cancel. |
| Lifecycle Manager | Strong | Proper state machine + transition table + event bus; asyncio-lock serialized; injected collaborators. |

### Cross-cutting

**Scalability — the primary limitation.** Nearly all runtime state is **in-process**:
`WorkspaceService` state, `shutdown_gate`, `activity_registry`, the lifecycle
singleton, and the sleep signals (`ws_manager.active_connections`,
`task_queue`). This is *correct and simple for the intended single-instance EC2
deployment*, but **breaks behind a load balancer with >1 instance**: each
instance sees only its own WebSocket connections / tasks, so the decision engine
could shut down an instance while another still has active work. If horizontal
scaling is ever required, these signals and state must move to a shared store
(Redis / MongoDB).

**Maintainability — strong.** Clear package boundaries (`power/`, `sleep/`,
`shutdown/`, `wake/`, `lifecycle/`), each with a single responsibility. Shutdown
reuses `SleepHooks` rather than duplicating. Main risk is *conceptual overlap*
between “sleep” and “shutdown” (both end in a PowerController stop) — document
which is authoritative to avoid drift.

**Fault tolerance — strong.** Fail-safe hooks, factory fallback to `local`,
engines block on uncertainty, EC2 returns structured errors instead of raising,
frontend degrades gracefully. Gaps: no persistence of lifecycle/workspace state
across restarts; ERROR recovery is manual.

**Concurrency — mostly sound.** `WorkspaceLifecycleManager` uses an
`asyncio.Lock`. `activity_registry` uses a `threading.Lock`. `shutdown_gate` /
`WorkspaceService` rely on single-event-loop semantics (safe under asyncio).
Watch item: several fire-and-forget `create_task` calls are unretained (§3).

**Error handling — consistent.** Uniform `_result` envelopes on the backend,
`describeError` categorization + retry limits on the frontend.

**Dependency Injection — excellent.** Every collaborator is injectable and
defaulted via a `get_*` provider; the factory selects the power strategy from
config. Tests can inject fakes cleanly.

---

## 3. Potential Bugs / Correctness Risks

1. **Unretained `asyncio` tasks (GC risk).** `WakeService.trigger_startup`,
   `ActivityService.track`, and `TaskQueue` schedule tasks without keeping a
   reference. Python can garbage-collect a pending task, cancelling it
   mid-flight. *Recommendation:* keep a set of task references and discard on
   completion (`task.add_done_callback(set.discard)`).

2. **Partial-shutdown leaves the HTTP gate closed.** `ShutdownOrchestrator` step
   1 closes admission (`shutdown_gate.close_http()`); if a later step raises,
   the sequence ends in the exception path **without reopening the gate**, so
   the instance keeps returning 503 until process restart. *Recommendation:*
   only close HTTP once the sequence is committed to completing, or reopen on
   failure (this is a real fix, deferred per “no code changes”).

3. **Two state holders can diverge.** `WorkspaceService` (in-memory
   `sleeping/starting/running`) and `WorkspaceLifecycleManager`
   (`STOPPED/STARTING/RUNNING/…`) are independent. `/workspace/status` reads the
   former; `/workspace/lifecycle` reads the latter. They are not synchronized —
   pick one authoritative source and have the other derive from it.

4. **Health semantics are shallow.** `GET /workspace/health` always returns 200
   (module liveness). The frontend treats 200 as “ready,” so it redirects the
   moment FastAPI accepts connections, not when Mongo/AI are actually ready. For
   real cold starts consider polling the deep `GET /health` (which checks Mongo,
   scheduler, AI, WS) or a dedicated readiness endpoint.

5. **In-memory state lost on restart/redeploy.** Workspace + lifecycle state
   reset to their defaults on every boot. Acceptable if the instance is the
   source of truth, but a redeploy will momentarily disagree with reality.

6. **Activity write amplification.** The activity middleware issues one MongoDB
   upsert per successful request (fire-and-forget). Under load this is heavy,
   and all unauthenticated traffic contends on a single `"anonymous"` document.
   *Recommendation:* throttle (only persist if last write > N seconds) or batch
   like the metering worker.

7. **CORS on the 503.** `ShutdownMiddleware` is outermost; its 503 does not
   carry CORS headers (CORS middleware is inner). Fine for the `fetch`-based
   poller (it just sees a failed request), but browser XHR callers won’t read
   the body. Low severity.

---

## 4. Performance Improvements

- **Batch/throttle activity writes** (see §3.6) — biggest DB win on the hot path.
- **Keep task references** for fire-and-forget work to avoid silent
  cancellation and reduce churn.
- **Cache the sleep signal snapshot** briefly (e.g. 1–2s) if the watcher and the
  `/workspace/sleep-check` endpoint are polled frequently, to avoid repeated
  `most_recent_activity` queries. (Index already exists — impact is small.)
- **boto3 client reuse** is already correct (created once, `asyncio.to_thread`).
- **Frontend**: `HealthPollingService` uses `fetch` + `AbortController` with a
  per-probe timeout — good. Consider exponential backoff after N failures to
  reduce request volume during long cold starts (currently fixed 5s).

---

## 5. Security Review

- **CRITICAL — all `/workspace/*` endpoints are unauthenticated.** `wake`,
  `start`, `stop`, `sleep-check`, `status`, and `lifecycle` have no
  `Depends(get_current_user)`. Anyone can trigger an instance start (cost), stop
  it, or enumerate internal state. *Recommendation:* require admin auth on
  `start`/`stop`/`sleep-check`/`lifecycle`/`status`. Because `wake` must work
  while the backend is down, it should live behind an **external, authenticated**
  waker (API Gateway + Lambda), not the app itself (see §6). Add **rate
  limiting** to `wake` to prevent cost-abuse.
- **IAM least privilege.** The EC2 role should allow only
  `ec2:StartInstances`, `ec2:StopInstances`, `ec2:DescribeInstances`, scoped by
  resource ARN / tag condition to the single target instance.
- **No hardcoded credentials** — confirmed. `boto3` uses the default provider
  chain; `import boto3` appears only in `ec2.py`. Good.
- **WebSocket endpoint is unauthenticated** (pre-existing) — the lifecycle event
  broadcast rides the same channel; ensure no sensitive detail is broadcast.
- **Secrets** (`.env`) confirmed gitignored.

---

## 6. Deployment Checklist

**Backend (EC2)**
- [ ] `pip install boto3` (in `requirements.txt`).
- [ ] Env: `POWER_CONTROLLER=ec2`, `AWS_REGION`, `INSTANCE_ID`.
- [ ] Env (optional): `AUTO_SHUTDOWN_ENABLED=true`, `AUTO_SHUTDOWN_INTERVAL_SECONDS`,
      `SLEEP_IDLE_TIMEOUT_SECONDS`, `SHUTDOWN_DRAIN_TIMEOUT_SECONDS`,
      `AWS_MAX_ATTEMPTS`.
- [ ] IAM role attached with least-privilege EC2 start/stop/describe on the
      instance ARN.
- [ ] **Add authentication** to workspace control endpoints (§5).
- [ ] Decide the authoritative state holder (WorkspaceService vs Lifecycle) (§3.3).

**External Waker (required)**
- [ ] An **always-on** service (API Gateway + Lambda, or a scheduled function)
      that can call `ec2:StartInstances` — because when the instance is stopped,
      `POST /workspace/wake` on the backend is itself unavailable. The frontend
      should call the external waker to start the instance, then poll
      `GET /workspace/health` for readiness. Document this clearly; the current
      backend `/workspace/wake` only works when the instance is already up.

**Load balancer / Nginx**
- [ ] Health-check path and thresholds tuned so draining (503s) does not flap
      the target group; honor `Retry-After`.
- [ ] Request/idle timeouts ≥ expected cold-start window.

**Frontend (Vercel)**
- [ ] `VITE_API_BASE_URL` points at the backend; the startup service’s wake +
      health URLs resolve correctly.
- [ ] Confirm the launcher is (or is not) the post-auth landing per product
      decision.

**Scaling**
- [ ] If running **>1 instance**, migrate in-process signals/state (WS count,
      shutdown gate, workspace/lifecycle state, activity registry) to a shared
      store before enabling auto-shutdown; otherwise pin to a single instance.

**Observability**
- [ ] CloudWatch alarms on instance state transitions, wake failures, and
      shutdown events; log-based metrics on the lifecycle event stream.
- [ ] End-to-end staging test: stop instance → external wake → health polling →
      dashboard redirect → idle → auto-shutdown.

---

## 7. Summary

The architecture is **well-structured, SOLID, and production-grade for a
single-instance EC2 deployment**, with excellent use of the Strategy Pattern and
DI and consistent fail-safe behavior. Before production, prioritize: **(1)
authentication on workspace endpoints**, **(2) the external waker** (the backend
can’t wake itself), **(3) the partial-shutdown gate-reopen fix**, and **(4) a
single authoritative state source**. Horizontal scaling requires moving
in-process state to a shared store; until then, keep the workspace to one
instance.
