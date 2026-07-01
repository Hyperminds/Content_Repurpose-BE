# Realtime / WebSocket Architecture — Analysis & Migration Plan

Documentation only. No code is changed here. This analyzes the current realtime
stack, its scaling limits, and a concrete path to a horizontally-scalable,
API-Gateway-compatible design.

---

## 1. Current Architecture

There are **three independent, in-process realtime mechanisms**:

### 1a. Main WebSocket — `ConnectionManager` (`app/ws/manager.py`)
- Endpoint: `WS /ws/{user_id}?channels=dashboard,notifications` (defined in `main.py`).
- Singleton `ws_manager` holds **all state in process memory**:
  - `_user_connections: Dict[user_id, Set[WebSocket]]`
  - `_channel_subscribers: Dict[channel, Set[user_id]]`
  - `_all_connections: Set[WebSocket]`
- API: `connect`, `disconnect`, `send_to_user`, `send_to_channel`, `broadcast`,
  `get_stats`.
- The endpoint loop handles client `ping` and `subscribe` messages.

### 1b. Server-Sent Events (SSE) — `event_bus.py`
- Endpoint: `GET /events/stream` (token via query param).
- In-process `_subscribers: Dict[user_id, list[asyncio.Queue]]`.
- Publishing side effects emit through here:
  `emit_post_scheduled / emit_post_published / emit_post_failed / emit_account_expired`.
- This is the path actually used by the publishing/scheduler flow today.

### 1c. Admin WebSocket — `AdminConnectionManager` (`super_admin_routes.py`)
- Endpoint: `WS /super-admin/ws?token=...` (validates a super_admin JWT).
- Separate in-memory `list[WebSocket]`; sends a heartbeat every 10s.

### Frontend consumers
- `config/env.js` builds `ws://<api>/ws/{userId}?channels=...`.
- `NotificationBell` uses SSE / polling for the unread count.

```
                       ┌─────────────────────────────┐
   browser ──WS────────▶  /ws/{user_id}  → ws_manager │  (in-memory dicts)
   browser ──SSE───────▶  /events/stream → event_bus  │  (in-memory queues)
   admin   ──WS────────▶  /super-admin/ws → AdminCM    │  (in-memory list)
                       └─────────────────────────────┘
                              single process / single event loop
```

---

## 2. Scalability Problems

### 2.1 State is per-process (the core blocker)
All three managers keep connections/subscribers in local memory. With more than
one worker or instance:
- A user connected to **instance A** will **not** receive events emitted on
  **instance B**. `send_to_user`, `send_to_channel`, and SSE `emit` only reach
  connections on the same process.
- `broadcast()` only reaches that one process's sockets.
- The `ws/manager.py` docstring claims "can be backed by Redis pub/sub" — but
  **no shared pub/sub exists**; it is purely local.

### 2.2 Lambda-incompatible
Persistent WebSocket/SSE connections require a long-lived process and event
loop. AWS Lambda is short-lived and frozen between invocations, so neither the
WS endpoint nor `/events/stream` can run there (see `LAMBDA.md`, which already
flags both as unsupported by the HTTP handler).

### 2.3 Security gap on the main WS endpoint
`/ws/{user_id}` takes `user_id` **straight from the path with no authentication**.
Any client can connect as any user and receive that user's channel events. (The
admin WS does validate a token; the main one does not.)

### 2.4 Duplicated / divergent realtime paths
WS (`ws_manager`) and SSE (`event_bus`) are parallel systems with different
payload shapes (`{event,data,timestamp}` vs `{type,data,timestamp}`). Emitters
must know which to call; today publishing uses SSE only, so WS clients may not
receive publish events at all.

### 2.5 Operational limits
- **Dead-connection cleanup** is best-effort (only when a send throws); stale
  entries can accumulate.
- **Broadcast is O(N) sequential** `await ws.send_text` — head-of-line latency
  with many connections.
- **No backpressure** beyond SSE `QueueFull` drop.
- **No delivery guarantee / resume** — events emitted while a client is
  disconnected are lost (no replay, no cursor).
- **Stats are per-process**, so `/system/stats` and health WS counts undercount
  in a multi-instance deployment.

---

## 3. Migration Strategy

Goal: decouple **event production** from **connection delivery** so any
instance can emit and every relevant connection receives — regardless of which
process/instance holds the socket.

### Phase 0 — Abstraction (no behavior change)
Introduce a `RealtimeTransport` interface with the existing surface
(`send_to_user`, `send_to_channel`, `broadcast`) and route all emitters through
it. Today's in-memory manager becomes one implementation (`InProcessTransport`).
Unify WS + SSE emission behind a single `emit_event(user_id, type, data)` API.

### Phase 1 — Shared fan-out (multi-instance on a long-lived runtime)
Add a **Redis Pub/Sub** (or NATS) backed transport:
- Each instance subscribes to Redis channels (`user:{id}`, `channel:{name}`).
- `emit_event` **publishes to Redis** instead of touching local sockets.
- Each instance's subscriber callback delivers to its **local** sockets only.
- Result: emit on any instance → delivered everywhere. Sticky sessions optional.
- Store presence/counts in Redis for accurate global stats.

### Phase 2 — Managed WebSockets (serverless / Lambda)
Move connection ownership out of the app entirely to **API Gateway WebSocket
API** (see §4), with a **DynamoDB connection registry**. The app no longer holds
sockets; it sends via the API Gateway management API.

### Phase 3 — Delivery guarantees (optional)
- Persist a per-user event log (Mongo/Redis stream) with a cursor so clients can
  **resume** missed events after reconnect.
- Add idempotency keys to events.

---

## 4. API Gateway Compatibility

API Gateway **WebSocket API** is a fundamentally different model from FastAPI's
in-process sockets:

| FastAPI (now) | API Gateway WebSocket |
|---------------|-----------------------|
| Server holds the live `WebSocket` object | Gateway holds the socket; app only sees a `connectionId` |
| `await ws.send_text(...)` | `POST` to `@connections/{connectionId}` via `ApiGatewayManagementApi.post_to_connection` |
| In-memory connection set | External store (DynamoDB) of `connectionId ↔ user_id ↔ channels` |
| One persistent handler loop | Discrete Lambda invocations per route |

**Routes to implement (handlers, not a loop):**
- `$connect` — authenticate (JWT in query string / header), write
  `{connectionId, user_id, channels, connectedAt}` to DynamoDB.
- `$disconnect` — delete the connectionId row.
- `$default` / custom routes — handle client messages (`ping`, `subscribe`):
  update the DynamoDB row's channels.

**Emitting an event:**
1. Look up target connectionIds in DynamoDB (by user_id or channel).
2. For each, call `post_to_connection(ConnectionId=..., Data=payload)`.
3. On `GoneException`, delete the stale connectionId.

**Mangum note:** the existing HTTP handler (`app/lambda_handler.py`) does **not**
serve WebSockets. API Gateway WS needs its own Lambda(s) wired to the
`$connect/$disconnect/$default` routes — separate from the Mangum HTTP handler.

**SSE under API Gateway:** SSE (long-lived HTTP streaming) is not supported by
API Gateway/Lambda. Migrate SSE consumers (e.g. notification count) to the
WebSocket API or a poll, or keep SSE only on a long-lived (non-Lambda) deployment.

---

## 5. Required Refactoring (checklist — not implemented here)

**Decoupling & abstraction**
- [ ] Define `RealtimeTransport` interface; make `ConnectionManager` one impl.
- [ ] Unify WS + SSE behind a single `emit_event(user_id, type, data)`; settle on
      one payload schema (`{type, data, timestamp}`).
- [ ] Route all emitters (publishing side effects, notifications, admin) through
      the transport rather than calling `ws_manager` / `event_bus` directly.

**Security**
- [ ] Authenticate the main `/ws` handshake (JWT), derive `user_id` from the
      token instead of trusting the path param. (Admin WS already does this.)

**Horizontal scale (Phase 1)**
- [ ] Add a Redis Pub/Sub transport; publish on emit, deliver to local sockets
      on subscribe callback.
- [ ] Move presence/stats to Redis for accurate global counts.

**Serverless (Phase 2)**
- [ ] DynamoDB connection registry (`connectionId ↔ user_id ↔ channels`, with TTL).
- [ ] `$connect` / `$disconnect` / `$default` Lambda handlers.
- [ ] Replace `ws.send_text` with `ApiGatewayManagementApi.post_to_connection`;
      handle `GoneException` cleanup.
- [ ] Decide SSE fate (migrate to WS API or keep on a long-lived service).

**Reliability (Phase 3, optional)**
- [ ] Per-user event log + resume cursor for missed-event replay.
- [ ] Backpressure / batching on fan-out; structured dead-connection reaping.

---

## Summary
The realtime layer works on a **single process** but cannot scale horizontally
or run on Lambda because all connection state lives in local memory, and the
main WS endpoint is unauthenticated. The path forward is to **decouple event
production from delivery** behind a transport abstraction, back it with **Redis
Pub/Sub** for multi-instance, and adopt **API Gateway WebSocket API + DynamoDB**
for the serverless model — while folding the parallel WS/SSE paths into one
authenticated, schema-consistent event API.
