"""
Web Push (VAPID) service.

Stores browser push subscriptions and delivers notifications to a user's
device(s) via the Web Push protocol — even when the app/tab is closed. Used to
alert users that a scheduled post is now due to be published manually.

Keys come from the environment (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY /
VAPID_SUBJECT). The public key is safe to expose to the browser; the private
key must stay secret.
"""

import os
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv

from app.database import db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:admin@example.com").strip()

push_subscriptions_collection = db["push_subscriptions"]


def push_enabled() -> bool:
    """True when VAPID keys are configured."""
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)


def get_vapid_public_key() -> str:
    return VAPID_PUBLIC_KEY


# ── subscription storage ─────────────────────────────────────────────────────

async def save_subscription(user_id: str, subscription: dict) -> dict:
    """Upsert a push subscription for a user, keyed by its endpoint."""
    endpoint = subscription.get("endpoint")
    if not endpoint:
        raise ValueError("subscription.endpoint is required")

    keys = subscription.get("keys", {}) or {}
    doc = {
        "user_id": user_id,
        "endpoint": endpoint,
        "keys": {"p256dh": keys.get("p256dh", ""), "auth": keys.get("auth", "")},
        "expiration_time": subscription.get("expirationTime"),
        "updated_at": datetime.now(timezone.utc),
    }
    await push_subscriptions_collection.update_one(
        {"user_id": user_id, "endpoint": endpoint},
        {"$set": doc, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return {"endpoint": endpoint}


async def delete_subscription(user_id: str, endpoint: str):
    await push_subscriptions_collection.delete_one({"user_id": user_id, "endpoint": endpoint})
    return {"message": "unsubscribed"}


async def get_subscriptions(user_id: str) -> list:
    cursor = push_subscriptions_collection.find({"user_id": user_id})
    return await cursor.to_list(length=100)


async def _delete_by_endpoint(endpoint: str):
    await push_subscriptions_collection.delete_one({"endpoint": endpoint})


# ── delivery ─────────────────────────────────────────────────────────────────

def _send_one(subscription_info: dict, payload: str):
    """Blocking single push send (pywebpush uses `requests`)."""
    from pywebpush import webpush
    webpush(
        subscription_info=subscription_info,
        data=payload,
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_claims={"sub": VAPID_SUBJECT},
        ttl=86400,
    )


async def send_web_push(user_id: str, title: str, body: str, data: dict = None) -> int:
    """
    Send a push notification to all of a user's registered devices.
    Prunes subscriptions the push service reports as gone (404/410).
    Returns the number of successful sends.
    """
    if not push_enabled():
        return 0

    subs = await get_subscriptions(user_id)
    if not subs:
        return 0

    payload = json.dumps({
        "title": title,
        "body": body,
        "data": data or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    sent = 0
    for sub in subs:
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": sub.get("keys", {}),
        }
        try:
            await asyncio.to_thread(_send_one, subscription_info, payload)
            sent += 1
        except Exception as e:
            # Gone / expired subscriptions should be removed so we stop retrying.
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                await _delete_by_endpoint(sub["endpoint"])
            else:
                print(f"[push] send failed for {sub['endpoint'][:40]}...: {e}")

    return sent
