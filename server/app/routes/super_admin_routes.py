"""
Super Admin API routes — isolated from regular user routes.
All endpoints require super_admin role.
"""

import time
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Body, WebSocket, WebSocketDisconnect
from bson import ObjectId
from app.utils.jwt_handler import get_current_user, require_role
from app.database import db

router = APIRouter(prefix="/super-admin", tags=["super-admin"])

# Collections
users_collection = db["users"]
generation_logs_collection = db["generation_logs"]
moderation_logs_collection = db["moderation_logs"]
post_history_collection = db["post_history"]
campaigns_collection = db["campaigns"]
notifications_collection = db["notifications"]

# WebSocket connection manager
class AdminConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

manager = AdminConnectionManager()


# ── OVERVIEW ─────────────────────────────────────────────────────────────────

@router.get("/overview")
async def get_overview(user: dict = Depends(require_role(["super_admin"]))):
    """Get top-level platform metrics for the admin overview."""
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(hours=24)

    # Parallel aggregations
    total_users, active_users_24h, total_campaigns, total_posts, \
    total_tokens, moderation_flags, pending_posts = await asyncio.gather(
        users_collection.count_documents({}),
        users_collection.count_documents({"created_at": {"$gte": day_ago}}),
        campaigns_collection.count_documents({}),
        post_history_collection.count_documents({}),
        _sum_field(generation_logs_collection, "total_tokens"),
        moderation_logs_collection.count_documents({}),
        post_history_collection.count_documents({"status": {"$in": ["scheduled", "ready_to_publish"]}}),
    )

    # AI cost
    cost_pipeline = [{"$group": {"_id": None, "total": {"$sum": "$estimated_cost"}}}]
    cost_result = await generation_logs_collection.aggregate(cost_pipeline).to_list(1)
    total_cost = round(cost_result[0]["total"] if cost_result else 0, 4)

    # Posts today
    posts_today = await post_history_collection.count_documents({"created_at": {"$gte": day_ago}})

    return {
        "total_users": total_users,
        "new_users_24h": active_users_24h,
        "total_campaigns": total_campaigns,
        "total_posts": total_posts,
        "posts_today": posts_today,
        "pending_posts": pending_posts,
        "total_tokens_used": total_tokens,
        "total_ai_cost_usd": total_cost,
        "moderation_flags": moderation_flags,
    }


async def _sum_field(collection, field: str) -> int:
    pipeline = [{"$group": {"_id": None, "total": {"$sum": f"${field}"}}}]
    result = await collection.aggregate(pipeline).to_list(1)
    return int(result[0]["total"]) if result else 0


# ── SYSTEM HEALTH ─────────────────────────────────────────────────────────────

@router.get("/health")
async def system_health(user: dict = Depends(require_role(["super_admin"]))):
    """Check health of all system components."""
    # MongoDB ping
    mongo_ok = False
    mongo_latency = 0
    try:
        t0 = time.time()
        await db.command("ping")
        mongo_latency = round((time.time() - t0) * 1000, 1)
        mongo_ok = True
    except Exception:
        pass

    # AI service check (OpenRouter)
    import os
    ai_configured = bool(os.getenv("OPENROUTER_API_KEY"))

    return {
        "api": {"status": "operational", "latency_ms": 0},
        "mongodb": {"status": "operational" if mongo_ok else "degraded", "latency_ms": mongo_latency},
        "scheduler": {"status": "operational"},
        "ai_service": {"status": "operational" if ai_configured else "not_configured"},
        "websocket": {"status": "operational", "connections": len(manager.active)},
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ── USER MANAGEMENT ───────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    limit: int = 50,
    offset: int = 0,
    search: str = None,
    user: dict = Depends(require_role(["super_admin"])),
):
    """List all users with usage stats."""
    query = {}
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"email": {"$regex": search, "$options": "i"}},
        ]

    cursor = users_collection.find(query).sort("created_at", -1).skip(offset).limit(limit)
    docs = await cursor.to_list(length=limit)
    total = await users_collection.count_documents(query)

    users_out = []
    for u in docs:
        uid = str(u["_id"])
        # Get token usage for this user
        token_pipeline = [
            {"$match": {"user_id": uid}},
            {"$group": {"_id": None, "total": {"$sum": "$total_tokens"}, "cost": {"$sum": "$estimated_cost"}}},
        ]
        token_result = await generation_logs_collection.aggregate(token_pipeline).to_list(1)
        tokens = int(token_result[0]["total"]) if token_result else 0
        cost = round(token_result[0]["cost"] if token_result else 0, 4)

        users_out.append({
            "id": uid,
            "name": u.get("name", ""),
            "email": u.get("email", ""),
            "role": u.get("role", "member"),
            "moderation_status": u.get("moderation_status", "active"),
            "moderation_flags": u.get("moderation_flags", 0),
            "is_verified": u.get("is_verified", False),
            "created_at": u.get("created_at").isoformat() if u.get("created_at") else None,
            "tokens_used": tokens,
            "ai_cost_usd": cost,
        })

    return {"users": users_out, "total": total}


@router.put("/users/{user_id}/role")
async def update_role(
    user_id: str,
    data: dict = Body(...),
    user: dict = Depends(require_role(["super_admin"])),
):
    """Update a user's role."""
    new_role = data.get("role", "")
    valid_roles = ["member", "super_admin"]
    if new_role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Use: {', '.join(valid_roles)}")
    await users_collection.update_one({"_id": ObjectId(user_id)}, {"$set": {"role": new_role}})
    return {"message": f"Role updated to {new_role}"}


@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: str,
    data: dict = Body(...),
    user: dict = Depends(require_role(["super_admin"])),
):
    """Suspend, warn, or reactivate a user."""
    action = data.get("action", "")
    valid = ["suspend", "warn", "reactivate"]
    if action not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid action. Use: {', '.join(valid)}")

    status_map = {"suspend": "suspended", "warn": "warned", "reactivate": "active"}
    await users_collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"moderation_status": status_map[action]}},
    )

    # Create notification for the user
    await notifications_collection.insert_one({
        "user_id": user_id,
        "title": f"Account {action}ed",
        "message": f"Your account has been {action}ed by an administrator.",
        "platform": "",
        "post_id": "",
        "status": "unread",
        "created_at": datetime.now(timezone.utc),
    })

    return {"message": f"User {action}ed successfully"}


@router.put("/users/{user_id}/reset-flags")
async def reset_flags(user_id: str, user: dict = Depends(require_role(["super_admin"]))):
    """Reset moderation flags for a user."""
    await users_collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"moderation_flags": 0, "moderation_status": "active"}},
    )
    return {"message": "Flags reset"}


# ── MODERATION LOGS ───────────────────────────────────────────────────────────

@router.get("/moderation")
async def get_moderation_logs(
    limit: int = 50,
    user: dict = Depends(require_role(["super_admin"])),
):
    """Get all moderation violation logs."""
    cursor = moderation_logs_collection.find().sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [
        {
            "id": str(d["_id"]),
            "user_id": d.get("user_id"),
            "category": d.get("category"),
            "content_preview": d.get("content_preview"),
            "action": d.get("action"),
            "flag_count": d.get("flag_count"),
            "created_at": d.get("created_at").isoformat() if d.get("created_at") else None,
        }
        for d in docs
    ]


# ── AI ACTIVITY ───────────────────────────────────────────────────────────────

@router.get("/ai-activity")
async def get_ai_activity(
    limit: int = 50,
    user: dict = Depends(require_role(["super_admin"])),
):
    """Get recent AI generation activity across all users."""
    cursor = generation_logs_collection.find().sort("generated_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    return [
        {
            "id": str(d["_id"]),
            "user_id": d.get("user_id"),
            "platform": d.get("platform"),
            "model": d.get("model"),
            "total_tokens": d.get("total_tokens", 0),
            "estimated_cost": d.get("estimated_cost", 0),
            "generation_time_ms": d.get("generation_time_ms", 0),
            "generated_at": d.get("generated_at").isoformat() if d.get("generated_at") else None,
        }
        for d in docs
    ]


@router.get("/token-usage")
async def get_token_usage(user: dict = Depends(require_role(["super_admin"]))):
    """Get platform-wide token usage breakdown."""
    # By platform
    platform_pipeline = [
        {"$group": {"_id": "$platform", "tokens": {"$sum": "$total_tokens"}, "cost": {"$sum": "$estimated_cost"}, "count": {"$sum": 1}}},
        {"$sort": {"tokens": -1}},
    ]
    platform_results = await generation_logs_collection.aggregate(platform_pipeline).to_list(10)

    # By day (last 14 days)
    day_pipeline = [
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$generated_at"}}, "tokens": {"$sum": "$total_tokens"}, "cost": {"$sum": "$estimated_cost"}}},
        {"$sort": {"_id": 1}},
        {"$limit": 14},
    ]
    day_results = await generation_logs_collection.aggregate(day_pipeline).to_list(14)

    total_tokens = sum(r["tokens"] for r in platform_results)
    total_cost = sum(r["cost"] for r in platform_results)

    return {
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cost, 4),
        "by_platform": [{"platform": r["_id"], "tokens": r["tokens"], "cost": round(r["cost"], 4), "count": r["count"]} for r in platform_results],
        "by_day": [{"date": r["_id"], "tokens": r["tokens"], "cost": round(r["cost"], 4)} for r in day_results],
    }


# ── POSTING QUEUE ─────────────────────────────────────────────────────────────

@router.get("/posting-queue")
async def get_posting_queue(user: dict = Depends(require_role(["super_admin"]))):
    """Get all pending/scheduled posts across all users."""
    cursor = post_history_collection.find(
        {"status": {"$in": ["scheduled", "ready_to_publish", "awaiting_manual_publish", "posting"]}}
    ).sort("scheduled_at", 1).limit(100)
    docs = await cursor.to_list(length=100)
    return [
        {
            "id": str(d["_id"]),
            "user_id": d.get("user_id"),
            "unique_post_id": d.get("unique_post_id"),
            "platform": d.get("platform"),
            "status": d.get("status"),
            "publish_type": d.get("publish_type"),
            "scheduled_at": d.get("scheduled_at").isoformat() if d.get("scheduled_at") else None,
            "content_preview": d.get("content_preview", ""),
        }
        for d in docs
    ]


# ── ADMIN LOGS ─────────────────────────────────────────────────────────────────

@router.get("/admin-logs")
async def admin_logs(limit: int = 100, user: dict = Depends(require_role(["super_admin"]))):
    """Get admin action audit trail."""
    from app.services.admin_control_service import get_admin_logs
    return await get_admin_logs(limit)


# ── AI CONTROL CENTER ─────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings(user: dict = Depends(require_role(["super_admin"]))):
    """Get global system settings."""
    from app.services.admin_control_service import get_system_settings
    return await get_system_settings()


@router.put("/settings")
async def update_settings(data: dict = Body(...), user: dict = Depends(require_role(["super_admin"]))):
    """Update global system settings."""
    from app.services.admin_control_service import update_system_settings, log_admin_action
    result = await update_system_settings(data)
    await log_admin_action(user["user_id"], "settings_update", "system", str(data))
    return result


# ── USER IMPERSONATION ────────────────────────────────────────────────────────

@router.post("/impersonate/{user_id}")
async def impersonate_user(user_id: str, user: dict = Depends(require_role(["super_admin"]))):
    """Generate impersonation token for viewing as another user."""
    from app.services.admin_control_service import generate_impersonation_token, get_user_for_impersonation, log_admin_action
    target = await get_user_for_impersonation(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    token = await generate_impersonation_token(user_id)
    await log_admin_action(user["user_id"], "impersonate", user_id, f"Viewing as {target['name']}")
    return {"token": token, "user": target}


# ── PREDICTIVE INSIGHTS ───────────────────────────────────────────────────────

@router.get("/insights")
async def predictive_insights(user: dict = Depends(require_role(["super_admin"]))):
    """Get AI-driven predictive insights."""
    from app.services.admin_control_service import get_predictive_insights
    return await get_predictive_insights()


# ── ADVANCED ANALYTICS ────────────────────────────────────────────────────────

@router.get("/analytics")
async def advanced_analytics(user: dict = Depends(require_role(["super_admin"]))):
    """Get advanced analytics — revenue, costs, heatmaps."""
    from app.services.admin_control_service import get_advanced_analytics
    return await get_advanced_analytics()


# ── WEBSOCKET ─────────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def admin_websocket(websocket: WebSocket):
    """WebSocket for real-time admin dashboard updates."""
    # Validate token from query param
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001)
        return

    try:
        from app.utils.jwt_handler import decode_access_token
        payload = decode_access_token(token)
        if payload.get("role") != "super_admin":
            await websocket.close(code=4003)
            return
    except Exception:
        await websocket.close(code=4001)
        return

    await manager.connect(websocket)
    try:
        # Send initial health ping every 10 seconds
        while True:
            await asyncio.sleep(10)
            try:
                await websocket.send_json({
                    "type": "heartbeat",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "ws_connections": len(manager.active),
                })
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
