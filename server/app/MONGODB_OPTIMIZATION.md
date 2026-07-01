# MongoDB Usage Analysis & Optimization

Scope: **infrastructure code only** (`app/database.py`). No repository/query code
was modified. This documents what was analyzed, what was applied, and what is
recommended for follow-up (including items that would require repo or ops changes).

---

## 1. Connection Pooling & Client Reuse

### Findings
- **Client reuse is already correct.** Every application module imports the
  single shared `db` / `client` from `app/database.py`. A repo-wide scan for
  `AsyncIOMotorClient` found extra instantiations **only** in standalone CLI
  scripts (`fix_users.py`, `clear_users.py`, `setup_db.py`, `check_db.py`), which
  are one-off tools outside the request path — they don't fragment the runtime pool.
- **Pool was under-configured.** The client previously set only
  `serverSelectionTimeoutMS` and `connectTimeoutMS`. No pool sizing, idle
  recycling, retry, or compression options.

### Applied (`database.py`)
| Option | Value | Why |
|--------|-------|-----|
| `maxPoolSize` | 50 (env `MONGO_MAX_POOL_SIZE`) | Cap concurrent connections per worker; prevents connection storms |
| `minPoolSize` | 5 (env `MONGO_MIN_POOL_SIZE`) | Keep warm connections; removes cold-start latency on bursts |
| `maxIdleTimeMS` | 30000 | Recycle idle sockets before Atlas drops them |
| `waitQueueTimeoutMS` | 5000 | Fail fast under saturation instead of hanging |
| `retryWrites` / `retryReads` | true | Transparently survive transient Atlas blips / failovers |
| `heartbeatFrequencyMS` | 10000 | Topology refresh cadence |
| `appname` | `TrendZZo` | Attributes queries in the Atlas profiler |
| `uuidRepresentation` | `standard` | Forward-safe UUID handling |
| `compressors` | auto (zlib present; snappy/zstd if installed) | Cut network bytes; never crashes if codec lib missing |

> **Deliberately NOT enabled:** `tz_aware=True`. The scheduler relies on naive
> datetimes (`scheduled_at.tzinfo is None`); enabling it would silently change
> scheduling behaviour.

### Recommendations (ops / not auto-applied)
- **Per-worker pool math:** with Gunicorn/Uvicorn workers, total Atlas
  connections ≈ `workers × maxPoolSize`. Keep this under your Atlas tier's
  connection limit (M10 ≈ 1500). At 50/worker, stay ≤ ~20 workers.
- **Install `python-snappy` or `zstandard`** to upgrade wire compression from
  zlib to snappy/zstd (lower CPU, better ratio). Already auto-detected — just
  add the dependency and it turns on.

---

## 2. Async Queries

### Findings
- All data access already uses Motor (`async`/`await`) end-to-end — no blocking
  PyMongo calls were found. Aggregations use `.to_list(length=...)` with caps. Good.

### Recommendations (would require repo changes — not done here)
- **Unbounded `to_list` caps:** a few aggregations use large fixed caps
  (e.g. `to_list(200)`). Fine today; revisit with pagination if collections grow.
- **`count_documents` on hot paths:** `notifications` unread count runs on a
  30s poll per client. Now backed by the `(user_id, status)` index (below); if
  it ever gets heavy, switch the bell to the existing SSE/WebSocket push and
  drop the poll.

---

## 3. Index Usage

### Findings — missing indexes on hot queries
| Collection | Hot query | Status before |
|------------|-----------|---------------|
| `campaign_content` | `find_one({day_id})` (every campaign content read/edit/chat) | **no index** |
| `campaign_content` | `find({campaign_id})` (analytics) | **no index** |
| `campaign_messages` | `find({campaign_id, day_id}).sort(created_at)` | **no index** |
| `campaign_strategies` | `find_one({campaign_id, user_id})` | **no index** |
| `campaign_activity` | `find({campaign_id, user_id}).sort(...)` | **no index** |
| `campaign_memory` | `find_one({user_id})` | **no index** |
| `campaign_analytics` | `find_one({campaign_id})` | **no index** |
| `users` | admin list `.sort(created_at desc)` | only `email` |
| `pending_verifications` | `find_one({email})` | **no index** |
| `password_resets` | `find_one({email})` | **no index** |
| `moderation_logs` | `find().sort(created_at desc)` | **no index** |
| `admin_logs` | `find().sort(created_at desc)` | **no index** |
| `platform_catalog` | `find_one({platform_name})` | **no index** |

### Findings — index/query mismatch
- `notifications` had `(user_id, read)` but **no document uses a `read` field** —
  the real queries filter `(user_id, status="unread")`. The old index was dead
  weight serving zero queries.

### Applied (`database.py`)
- Added every missing index above.
- Added `notifications (user_id, status)` to match the real query.
- Made naturally-unique lookups **unique** where safe (`users.email`,
  `pending_verifications.email`, `password_resets.email`, `campaign_memory.user_id`,
  `campaign_analytics.campaign_id`, `platform_catalog.platform_name`).
- **Removed redundant single-field indexes** from the creation set where a
  compound index already covers them by prefix (e.g. standalone `user_id` when
  `(user_id, created_at)` exists). Fewer indexes = faster writes + less storage.

### Recommendations (destructive — not auto-applied)
- **Drop the dead `notifications (user_id, read)` index** in Atlas (or a one-off
  migration). `create_index` is additive, so it still physically exists from
  before; removing it reclaims write overhead:
  ```js
  db.notifications.dropIndex("user_id_1_read_1")
  ```
- **Audit other previously-created single-field indexes** now covered by
  compounds (`bookmarks.user_id`, `bookmarks.created_at`, `history.user_id`,
  `post_history.user_id`, `post_history.platform`, `post_history.status`,
  `scheduled_posts.status`, `campaigns.created_at`, `generation_logs.user_id`,
  `generation_logs.organization_id`, `generation_logs.campaign_id`,
  `metering_events.organization_id`) and drop the redundant ones after confirming
  with `$indexStats`.

---

## 4. Query Performance

### Applied
- **Resilient, concurrent index creation:** `init_db` now builds all indexes via
  `asyncio.gather` with a `_safe_index` wrapper. A single failing index (e.g. a
  unique index hitting pre-existing duplicates) logs and continues instead of
  crashing startup. Faster boot, safer rollout.

### Recommendations (ops / repo — not done here)
- **TTL retention on append-only logs.** `metering_events` and `generation_logs`
  grow unbounded. Add TTL indexes to auto-expire raw rows (keep aggregates):
  ```python
  await db["metering_events"].create_index("timestamp", expireAfterSeconds=60*60*24*90)
  await db["generation_logs"].create_index("generated_at", expireAfterSeconds=60*60*24*180)
  ```
  (Left out of auto-apply because retention windows are a business/ops decision.)
- **Verify plans with `explain`.** For the heaviest aggregations
  (`get_organization_usage`, metering `get_endpoint_breakdown`,
  `get_publishing_stats`) run `explain("executionStats")` to confirm
  `IXSCAN` (not `COLLSCAN`) and add covering fields if needed.
- **Read preference for analytics.** Admin/analytics reads could use
  `secondaryPreferred` to offload the primary — only if slight staleness is
  acceptable. Apply per-query (repo change), not globally.
- **Write concern for high-volume logs.** Metering/generation logs are
  non-critical; a per-collection `w=1` write concern would cut write latency
  vs the default majority. This is a repo-level change (per-collection handle).

---

## Summary of changes (infrastructure only)
- Tuned the shared Motor client: pool sizing, idle recycling, retries,
  heartbeat, appname, UUID rep, opportunistic compression.
- Added ~15 missing indexes on hot query paths; fixed the notifications
  index/query mismatch; made safe unique constraints.
- Made index creation concurrent and crash-proof.
- No repository/query code touched. Follow-up items requiring repo or ops
  decisions are listed as recommendations above.
