"""
Web Push subscription routes.

The browser registers a service worker, subscribes to push using the VAPID
public key, and sends the resulting subscription here to be stored. The backend
later pushes "scheduled post is due" alerts to these subscriptions.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, Dict

from app.utils.jwt_handler import get_current_user
from app.services.push_service import (
    get_vapid_public_key,
    push_enabled,
    save_subscription,
    delete_subscription,
    send_web_push,
)

router = APIRouter(prefix="/push", tags=["push"])


class PushSubscription(BaseModel):
    endpoint: str
    keys: Dict[str, str] = {}
    expirationTime: Optional[float] = None


class UnsubscribeBody(BaseModel):
    endpoint: str


@router.get("/vapid-public-key")
async def vapid_public_key():
    """Public VAPID key the browser needs to create a push subscription."""
    return {"publicKey": get_vapid_public_key(), "enabled": push_enabled()}


@router.post("/subscribe")
async def subscribe(sub: PushSubscription, user: dict = Depends(get_current_user)):
    await save_subscription(user["user_id"], sub.model_dump())
    return {"message": "subscribed"}


@router.post("/unsubscribe")
async def unsubscribe(body: UnsubscribeBody, user: dict = Depends(get_current_user)):
    return await delete_subscription(user["user_id"], body.endpoint)


@router.post("/test")
async def test_push(user: dict = Depends(get_current_user)):
    """Send a test push to the current user's devices (helps verify setup)."""
    count = await send_web_push(
        user["user_id"],
        "TrendZZo notifications are on",
        "You'll get an alert here when a scheduled post is ready to publish.",
        {"url": "/"},
    )
    return {"sent": count}
