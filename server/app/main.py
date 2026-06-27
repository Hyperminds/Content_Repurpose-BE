from contextlib import asynccontextmanager    
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes.content_routes import router as content_router
from app.routes.bookmark_routes import router as bookmark_router
from app.routes.history_routes import router as history_router
from app.routes.auth_routes import router as auth_router
from app.routes.scheduled_post_routes import router as scheduled_post_router
from app.routes.publishing_routes import router as publishing_router
from app.routes.oauth_routes import router as oauth_router
from app.routes.events_routes import router as events_router
from app.routes.moderation_routes import router as moderation_router
from app.routes.analytics_routes import router as analytics_router
from app.routes.platform_routes import router as platform_routes_router
from app.routes.manual_accounts_routes import router as manual_accounts_router
from app.routes.ai_scoring_routes import router as ai_scoring_router
from app.routes.ai_usage_routes import router as ai_usage_router
from app.routes.upload_routes import router as upload_router
from app.routes.campaign_routes import router as campaign_router
from app.routes.super_admin_routes import router as super_admin_router
from app.routes.social_presence_routes import router as social_presence_router
from app.routes.trend_routes import router as trend_router
from app.routes.dev_routes import router as dev_router
from app.database import init_db
from app.models.user_model import init_users_collection
from app.services.scheduler_worker import start_scheduler, stop_scheduler
from app.config import log_env, APP_NAME, APP_VERSION, CORS_ORIGINS, CORS_ALLOW_CREDENTIALS, APP_ENV
from app.middleware.error_handler import ErrorHandlerMiddleware
from app.middleware.rate_limiter import RateLimitMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await init_users_collection()
    start_scheduler()
    log_env()
    print(f"✓ MongoDB connected & indexes created")
    print(f"✓ Scheduler worker started")
    yield
    stop_scheduler()


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="AI-powered content operating system",
    lifespan=lifespan,
    docs_url="/docs" if APP_ENV != "production" else None,
    redoc_url="/redoc" if APP_ENV != "production" else None,
)

# ── Middleware ────────────────────────────────────────────────────────────────
# Order matters: last added = outermost (runs first on request)
# CORS must be outermost so preflight OPTIONS requests are handled before anything else
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(ErrorHandlerMiddleware)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(oauth_router)
app.include_router(events_router)
app.include_router(moderation_router)
app.include_router(analytics_router)
app.include_router(content_router)
app.include_router(bookmark_router)
app.include_router(history_router)
app.include_router(scheduled_post_router)
app.include_router(publishing_router)
app.include_router(platform_routes_router)
app.include_router(manual_accounts_router)
app.include_router(ai_scoring_router)
app.include_router(ai_usage_router)
app.include_router(upload_router)
app.include_router(campaign_router)
app.include_router(super_admin_router)
app.include_router(social_presence_router)
app.include_router(trend_router)
app.include_router(dev_router)


# ── Health Check ──────────────────────────────────────────────────────────────
@app.get("/")
def home():
    return {"message": f"{APP_NAME} v{APP_VERSION} is running", "env": APP_ENV}


@app.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring and deployment readiness probes.
    Checks MongoDB connectivity and returns system status.
    """
    from app.database import client as mongo_client
    from datetime import datetime, timezone

    checks = {"mongodb": "unknown", "scheduler": "unknown", "ai_service": "unknown", "websockets": "unknown"}

    # MongoDB check
    try:
        await mongo_client.admin.command("ping")
        checks["mongodb"] = "healthy"
    except Exception as e:
        checks["mongodb"] = f"unhealthy: {str(e)}"

    # Scheduler check
    try:
        from app.services.scheduler_worker import _running as scheduler_running
        checks["scheduler"] = "running" if scheduler_running else "stopped"
    except Exception:
        checks["scheduler"] = "unknown"

    # AI service check
    from app.config import OPENROUTER_API_KEY, USE_MOCK
    if USE_MOCK:
        checks["ai_service"] = "mock_mode"
    elif OPENROUTER_API_KEY:
        checks["ai_service"] = "configured"
    else:
        checks["ai_service"] = "not_configured"

    # WebSocket check
    from app.websockets.manager import ws_manager
    checks["websockets"] = f"active ({ws_manager.active_connections} connections)"

    all_healthy = all(
        "healthy" in str(v) or v in ("running", "configured", "mock_mode") or "active" in str(v)
        for v in checks.values()
    )

    return {
        "status": "healthy" if all_healthy else "degraded",
        "app": APP_NAME,
        "version": APP_VERSION,
        "environment": APP_ENV,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


# ── WebSocket Endpoint ────────────────────────────────────────────────────────
from fastapi import WebSocket, WebSocketDisconnect, Query

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str, channels: str = Query(default="dashboard")):
    """
    Main WebSocket endpoint for realtime updates.
    Connect with: ws://localhost:8000/ws/{user_id}?channels=dashboard,notifications
    """
    from app.websockets.manager import ws_manager
    from app.services.logger import log

    channel_list = [c.strip() for c in channels.split(",") if c.strip()]
    await ws_manager.connect(websocket, user_id, channel_list)
    log.ws_event("connected", user_id=user_id, connections=ws_manager.active_connections)

    try:
        while True:
            # Keep connection alive, handle incoming messages
            data = await websocket.receive_text()
            # Client can send ping/subscribe messages
            try:
                import json
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
                elif msg.get("type") == "subscribe":
                    new_channels = msg.get("channels", [])
                    for ch in new_channels:
                        if ch not in ws_manager._channel_subscribers:
                            ws_manager._channel_subscribers[ch] = set()
                        ws_manager._channel_subscribers[ch].add(user_id)
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, user_id)
        log.ws_event("disconnected", user_id=user_id, connections=ws_manager.active_connections)


# ── System Stats (admin) ──────────────────────────────────────────────────────
@app.get("/system/stats")
async def system_stats():
    """System-level stats for monitoring. Available in dev/staging only."""
    if APP_ENV == "production":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Not available in production")

    from app.websockets.manager import ws_manager
    from app.services.background_tasks import task_queue
    from app.services.feature_flags import get_all_flags

    return {
        "websockets": ws_manager.get_stats(),
        "task_queue": task_queue.get_stats(),
        "feature_flags": get_all_flags(),
        "environment": APP_ENV,
    }
