"""
MongoDB infrastructure: single shared Motor client, connection pool tuning,
and index management.

This module is the ONLY place a Motor client is created for the application.
Every service/repository imports `db` (or a collection) from here, so the whole
process shares one connection pool — which is exactly what Motor/PyMongo want.
Do not instantiate AsyncIOMotorClient anywhere else in the app.
"""

import os
import asyncio
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "content_repurposer")

# ── Connection pool tuning (env-overridable) ──────────────────────────────────
# A single pool is shared across the whole event loop. Sizing it well prevents
# both connection starvation under load and idle-connection churn.
MAX_POOL_SIZE = int(os.getenv("MONGO_MAX_POOL_SIZE", "50"))
MIN_POOL_SIZE = int(os.getenv("MONGO_MIN_POOL_SIZE", "5"))
MAX_IDLE_MS = int(os.getenv("MONGO_MAX_IDLE_MS", "30000"))
WAIT_QUEUE_TIMEOUT_MS = int(os.getenv("MONGO_WAIT_QUEUE_TIMEOUT_MS", "5000"))


def _available_compressors() -> str:
    """
    Enable wire compression only for codecs whose library is actually installed,
    so a missing optional dependency can never crash startup.
    zlib ships with the stdlib; snappy/zstd are opportunistic.
    """
    comps = []
    for name, module in (("snappy", "snappy"), ("zstd", "zstandard"), ("zlib", "zlib")):
        try:
            __import__(module)
            comps.append(name)
        except Exception:
            pass
    return ",".join(comps)


# ── Client options ────────────────────────────────────────────────────────────
# - serverSelectionTimeoutMS / connectTimeoutMS: fail fast if Atlas unreachable
# - max/minPoolSize: cap concurrency, keep warm connections (avoid cold starts)
# - maxIdleTimeMS: recycle idle sockets so Atlas doesn't drop them under us
# - waitQueueTimeoutMS: fail fast instead of hanging when the pool is saturated
# - retryWrites/retryReads: transparently retry transient Atlas blips
# - heartbeatFrequencyMS: topology refresh cadence
# - appname: shows up in the Atlas profiler for easy attribution
# NOTE: tz_aware is intentionally LEFT OFF — the scheduler relies on naive
#       datetimes (it branches on `scheduled_at.tzinfo is None`). Enabling it
#       would silently change scheduling behaviour.
_client_kwargs = dict(
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=10000,
    maxPoolSize=MAX_POOL_SIZE,
    minPoolSize=MIN_POOL_SIZE,
    maxIdleTimeMS=MAX_IDLE_MS,
    waitQueueTimeoutMS=WAIT_QUEUE_TIMEOUT_MS,
    retryWrites=True,
    retryReads=True,
    heartbeatFrequencyMS=10000,
    appname="TrendZZo",
    uuidRepresentation="standard",
)

_compressors = _available_compressors()
if _compressors:
    _client_kwargs["compressors"] = _compressors

client = AsyncIOMotorClient(MONGODB_URL, **_client_kwargs)
db = client[DB_NAME]

# Collections (shared singletons)
bookmarks_collection = db["bookmarks"]
history_collection = db["history"]
scheduled_posts_collection = db["scheduled_posts"]


# ── Index management ──────────────────────────────────────────────────────────

async def _safe_index(collection, keys, **opts):
    """
    Create an index, swallowing errors so one bad/duplicate index can never
    abort startup. create_index is idempotent, so this is safe to run every boot.
    """
    try:
        await collection.create_index(keys, **opts)
    except Exception as e:
        name = opts.get("name", keys)
        print(f"[db] index create skipped on {collection.name} {name}: {e}")


async def init_db(create_indexes: bool = True):
    """
    Verify connectivity and (optionally) ensure all indexes exist.

    Indexes are created concurrently (asyncio.gather) and each is wrapped so a
    single failure (e.g. a pre-existing duplicate for a unique index) logs and
    continues instead of crashing the app.

    `create_indexes=False` is used in the AWS Lambda runtime: index creation is
    a one-time migration concern (run via a deploy/migration job), not something
    to repeat on every cold start. The connectivity ping still runs so a broken
    DB connection fails fast.
    """
    await client.admin.command("ping")

    if not create_indexes:
        return

    tasks = [
        # ── Bookmarks ────────────────────────────────────────────────────────
        _safe_index(bookmarks_collection, [("user_id", 1), ("created_at", -1)]),
        _safe_index(bookmarks_collection, "platform"),

        # ── History ──────────────────────────────────────────────────────────
        _safe_index(history_collection, [("user_id", 1), ("created_at", -1)]),

        # ── Scheduled posts (legacy) ─────────────────────────────────────────
        _safe_index(scheduled_posts_collection, [("status", 1), ("scheduled_at", 1)]),
        _safe_index(scheduled_posts_collection, "user_id"),

        # ── Post history (publishing) ────────────────────────────────────────
        _safe_index(db["post_history"], "unique_post_id", unique=True),
        _safe_index(db["post_history"], [("status", 1), ("scheduled_at", 1)]),
        _safe_index(db["post_history"], [("user_id", 1), ("created_at", -1)]),
        _safe_index(db["post_history"], [("user_id", 1), ("platform", 1), ("status", 1)]),

        # ── Users ────────────────────────────────────────────────────────────
        _safe_index(db["users"], "email", unique=True),
        _safe_index(db["users"], [("created_at", -1)]),                  # admin user list sort
        _safe_index(db["pending_verifications"], "email", unique=True),  # signup lookup
        _safe_index(db["password_resets"], "email", unique=True),        # reset lookup

        # ── Campaigns ────────────────────────────────────────────────────────
        _safe_index(db["campaigns"], [("user_id", 1), ("campaign_status", 1)]),
        _safe_index(db["campaigns"], [("user_id", 1), ("created_at", -1)]),
        _safe_index(db["campaign_days"], [("campaign_id", 1), ("day_number", 1)]),
        # campaign_content.day_id is the hottest lookup in the campaign module
        _safe_index(db["campaign_content"], "day_id"),
        _safe_index(db["campaign_content"], "campaign_id"),
        _safe_index(db["campaign_messages"], [("campaign_id", 1), ("day_id", 1), ("created_at", 1)]),
        _safe_index(db["campaign_strategies"], [("campaign_id", 1), ("user_id", 1)]),
        _safe_index(db["campaign_activity"], [("campaign_id", 1), ("user_id", 1), ("created_at", -1)]),
        _safe_index(db["campaign_memory"], "user_id", unique=True),
        _safe_index(db["campaign_analytics"], "campaign_id", unique=True),

        # ── Connected accounts ───────────────────────────────────────────────
        _safe_index(db["connected_accounts"], [("user_id", 1), ("platform", 1)]),
        _safe_index(db["connected_accounts"], "platform_user_id"),

        # ── Notifications ────────────────────────────────────────────────────
        # Matches the real queries: count/list by (user_id, status="unread").
        # The previous (user_id, read) index did not match any query.
        _safe_index(db["notifications"], [("user_id", 1), ("status", 1)]),
        _safe_index(db["notifications"], [("user_id", 1), ("created_at", -1)]),

        # ── Moderation / admin logs (sorted by recency) ──────────────────────
        _safe_index(db["moderation_logs"], [("created_at", -1)]),
        _safe_index(db["moderation_logs"], "user_id"),
        _safe_index(db["admin_logs"], [("created_at", -1)]),

        # ── Platform catalog ─────────────────────────────────────────────────
        _safe_index(db["platform_catalog"], "platform_name", unique=True),

        # ── AI usage (generation_logs) ───────────────────────────────────────
        _safe_index(db["generation_logs"], [("user_id", 1), ("generated_at", -1)]),
        _safe_index(db["generation_logs"], [("organization_id", 1), ("generated_at", -1)]),
        _safe_index(db["generation_logs"], [("user_id", 1), ("campaign_id", 1)]),
        _safe_index(db["generation_logs"], "platform"),
        _safe_index(db["generation_logs"], "model"),
        _safe_index(db["generation_logs"], "generated_at"),

        # ── Metering events ──────────────────────────────────────────────────
        _safe_index(db["metering_events"], [("organization_id", 1), ("timestamp", -1)]),
        _safe_index(db["metering_events"], [("organization_id", 1), ("endpoint", 1)]),
        _safe_index(db["metering_events"], [("user_id", 1), ("timestamp", -1)]),
        _safe_index(db["metering_events"], "request_id"),
        _safe_index(db["metering_events"], [("exported", 1), ("timestamp", 1)]),

        # ── User activity (last_activity tracking) ───────────────────────────
        # One doc per user (keyed by _id); these support "recently active"
        # lookups by org/time without scanning.
        _safe_index(db["user_activity"], [("organization_id", 1), ("last_activity", -1)]),
        _safe_index(db["user_activity"], [("user_id", 1)]),
    ]

    await asyncio.gather(*tasks)
