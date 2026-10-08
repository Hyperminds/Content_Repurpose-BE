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
from app.routes.image_generation_routes import router as image_generation_router
from app.routes.campaign_routes import router as campaign_router
from app.routes.super_admin_routes import router as super_admin_router
from app.routes.social_presence_routes import router as social_presence_router
from app.routes.trend_routes import router as trend_router
from app.routes.dev_routes import router as dev_router
from app.routes.metering_routes import router as metering_router
from app.social_publishing.api.routes import router as social_publishing_router
from app.social_publishing.api.connection_routes import router as social_publishing_oauth_router
from app.social_publishing.api.reddit_routes import router as social_publishing_reddit_router
from app.social_publishing.api.instagram_routes import router as social_publishing_instagram_router
from app.social_publishing.api.facebook_routes import router as social_publishing_facebook_router
from app.social_publishing.api.quora_routes import router as social_publishing_quora_router
from app.social_publishing.agent.routes import router as social_publishing_agent_router
from app.database import init_db
from app.models.user_model import init_users_collection
from app.services.scheduler_worker import start_scheduler, stop_scheduler
from app.config import log_env, APP_NAME, APP_VERSION, CORS_ORIGINS, CORS_ALLOW_CREDENTIALS, APP_ENV
from app.middleware.error_handler import ErrorHandlerMiddleware
from app.middleware.rate_limiter import RateLimitMiddleware
from app.middleware.metering_middleware import MeteringMiddleware
from app.services.metering_service import start_metering_worker, stop_metering_worker
from app.social_publishing.scheduling import SocialPublishingScheduler
from app.social_publishing.api.dependencies import get_orchestrator, get_posts_repo
from app.social_publishing.jobs.scheduler import JobScheduler
from app.social_publishing.jobs.worker import PublishingWorker
from app.social_publishing.jobs.repository import JobRepository
from app.social_publishing.jobs.metrics import PublishingMetrics
from app.social_publishing.repositories.social_posts_repository import SocialPostsRepository as SPPostsRepo
from app.social_publishing.repositories.social_accounts_repository import SocialAccountsRepository as SPAccountsRepo
from app.social_publishing.publishers.registry import PublisherRegistry as SPPublisherRegistry


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(create_indexes=True)
    await init_users_collection()
    start_scheduler()
    start_metering_worker()

    # ── Social Publishing Engine (Phase 3: job-based) ─────────────────────────
    from app.social_publishing.startup_checks import run_startup_checks
    run_startup_checks()

    sp_metrics = PublishingMetrics()
    sp_job_repo = JobRepository()
    sp_posts_repo = SPPostsRepo()
    sp_accounts_repo = SPAccountsRepo()
    sp_publisher_registry = SPPublisherRegistry()

    # Register platform publishers
    from app.social_publishing.integrations.linkedin import LinkedInPublisher
    from app.social_publishing.integrations.facebook import FacebookPublisher
    from app.social_publishing.integrations.instagram import InstagramPublisher
    from app.social_publishing.integrations.twitter import TwitterPublisher
    sp_publisher_registry.register(LinkedInPublisher())
    sp_publisher_registry.register(FacebookPublisher())
    sp_publisher_registry.register(InstagramPublisher())
    sp_publisher_registry.register(TwitterPublisher())

    # ── Hybrid publishing provider router ─────────────────────────────────────
    # Always includes the native provider (wraps the platform publishers above).
    # Hermes / BYOK providers are registered ONLY when their feature flags are
    # enabled, so no experimental provider is active in production by default.
    from app.social_publishing.providers.router import ProviderRouter
    from app.social_publishing.providers.native_provider import NativeApiProvider
    import app.config as _cfg

    sp_provider_router = ProviderRouter()
    sp_provider_router.register(NativeApiProvider(sp_publisher_registry))

    if getattr(_cfg, "ENABLE_HERMES_PROVIDER", False):
        from app.social_publishing.providers.hermes.provider import HermesPublisherProvider
        from app.social_publishing.providers.hermes.adapter import FakeHermesExecutionAdapter
        from app.social_publishing.providers.hermes.automation_policy import PlatformAutomationPolicy
        from app.social_publishing.providers.hermes.reddit.workflow import HermesRedditWorkflow
        from app.social_publishing.providers.hermes.reddit import constants as _reddit_c

        # Central automation policy — default-deny. Reddit is the ONLY platform
        # explicitly allow-listed, and only as USER-ASSISTED (see workflow +
        # the /social-publishing/reddit routes, which require explicit confirm).
        _hermes_policy = PlatformAutomationPolicy()
        _hermes_policy.allow(_reddit_c.PLATFORM)

        # Reddit publishes via a USER_ASSISTED_AGENT provider. On the scheduled
        # job path the workflow.execute() only PREPARES and returns
        # ACTION_REQUIRED — it never auto-publishes (Phase 12). The real browser
        # (Playwright) is driven per-request through the Reddit routes/service.
        sp_hermes_user_assisted = HermesPublisherProvider(
            adapter_factory=lambda: FakeHermesExecutionAdapter(),
            automation_policy=_hermes_policy,
            user_assisted=True,
        )
        sp_hermes_user_assisted.register_workflow(HermesRedditWorkflow())

        # Instagram user-assisted Hermes workflow — ALONGSIDE the native Meta
        # Instagram API (unchanged). Provider selection is by account.provider_type,
        # so an Instagram account can use native_api OR user_assisted_agent.
        # Gated by its own flag (default OFF); the native path is unaffected.
        if getattr(_cfg, "ENABLE_HERMES_INSTAGRAM", False):
            from app.social_publishing.providers.hermes.instagram.workflow import (
                HermesInstagramWorkflow,
            )
            from app.social_publishing.providers.hermes.instagram import constants as _ig_c
            _hermes_policy.allow(_ig_c.PLATFORM)
            sp_hermes_user_assisted.register_workflow(HermesInstagramWorkflow())
            print("✓ Hermes user-assisted Instagram workflow registered (user-confirmed)")

        # Facebook user-assisted Hermes workflow (profile OR page) — ALONGSIDE
        # the native Meta/Facebook API (unchanged). ONE workflow, target-aware.
        # Provider selection is by account.provider_type. Gated by its own flag
        # (default OFF); the native path is unaffected.
        if getattr(_cfg, "ENABLE_HERMES_FACEBOOK", False):
            from app.social_publishing.providers.hermes.facebook.workflow import (
                HermesFacebookWorkflow,
            )
            from app.social_publishing.providers.hermes.facebook import constants as _fb_c
            _hermes_policy.allow(_fb_c.PLATFORM)
            sp_hermes_user_assisted.register_workflow(HermesFacebookWorkflow())
            print("✓ Hermes user-assisted Facebook workflow registered (profile+page; user-confirmed)")

        # Quora user-assisted Hermes workflow (profile/space Post) — there is NO
        # native Quora API path, so this is the only Quora provider. Provider
        # selection is by account.provider_type. Gated by its own flag
        # (default OFF).
        if getattr(_cfg, "ENABLE_HERMES_QUORA", False):
            from app.social_publishing.providers.hermes.quora.workflow import (
                HermesQuoraWorkflow,
            )
            from app.social_publishing.providers.hermes.quora import constants as _quora_c
            _hermes_policy.allow(_quora_c.PLATFORM)
            sp_hermes_user_assisted.register_workflow(HermesQuoraWorkflow())
            print("✓ Hermes user-assisted Quora workflow registered (user-confirmed)")

        sp_provider_router.register(sp_hermes_user_assisted)

        # Expire unconfirmed user-assisted posts (Phase 12) — bounded, no retry.
        from app.social_publishing.providers.hermes.confirmation_expiry_worker import (
            ConfirmationExpiryWorker,
        )
        sp_confirmation_expiry_worker = ConfirmationExpiryWorker()
        sp_confirmation_expiry_worker.start()
        print("✓ Hermes user-assisted provider registered (Reddit workflow; user-confirmed)")

    # Credential resolver for the worker
    async def _resolve_credential(account_id: str, tenant_id: str):
        from app.social_publishing.services.account_connection_service import AccountConnectionService
        from app.social_publishing.auth.oauth_state_store import OAuthStateStore
        from app.social_publishing.credentials.vault import CredentialVault
        from app.social_publishing.auth.provider import AuthProviderRegistry
        vault = CredentialVault()
        svc = AccountConnectionService(sp_accounts_repo, AuthProviderRegistry(), OAuthStateStore(), vault)
        return await svc.get_access_token(account_id, tenant_id)

    from app.social_publishing.jobs.rate_limiter import PublishingRateLimiter
    _sp_rate_limiter = PublishingRateLimiter()

    sp_job_scheduler = JobScheduler(sp_posts_repo, sp_job_repo, metrics=sp_metrics,
                                     rate_limiter=_sp_rate_limiter)
    sp_worker = PublishingWorker(
        job_repo=sp_job_repo,
        posts_repo=sp_posts_repo,
        accounts_repo=sp_accounts_repo,
        publisher_registry=sp_publisher_registry,
        credential_resolver=_resolve_credential,
        metrics=sp_metrics,
        provider_router=sp_provider_router,
    )

    sp_job_scheduler.start()
    sp_worker.start()

    # Token refresh worker
    from app.social_publishing.jobs.token_refresh_worker import TokenRefreshWorker
    from app.social_publishing.credentials.vault import CredentialVault as SPVault
    from app.social_publishing.auth.provider import AuthProviderRegistry as SPAuthRegistry
    from app.social_publishing.auth.linkedin_provider import LinkedInAuthProvider as SPLinkedInAuth
    from app.social_publishing.integrations.facebook import FacebookAuthProvider as SPFacebookAuth
    from app.social_publishing.integrations.instagram import InstagramAuthProvider as SPInstagramAuth
    from app.social_publishing.integrations.twitter import TwitterAuthProvider as SPTwitterAuth

    sp_auth_registry = SPAuthRegistry()
    sp_auth_registry.register(SPLinkedInAuth())
    sp_auth_registry.register(SPFacebookAuth())
    sp_auth_registry.register(SPInstagramAuth())
    sp_auth_registry.register(SPTwitterAuth())
    sp_token_worker = TokenRefreshWorker(sp_accounts_repo, sp_auth_registry, SPVault())
    sp_token_worker.start()

    # Store metrics on app state for the stats endpoint
    app.state.sp_metrics = sp_metrics

    log_env()
    print(f"✓ MongoDB connected & indexes created")
    print(f"✓ Scheduler worker started")
    print(f"✓ Retry worker started")
    print(f"✓ Metering worker started")
    print(f"✓ Social Publishing job scheduler started")
    print(f"✓ Social Publishing worker started")
    print(f"✓ Token refresh worker started")
    yield
    sp_token_worker.stop()
    sp_worker.stop()
    sp_job_scheduler.stop()
    stop_scheduler()
    await stop_metering_worker()


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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://trendzzo.hyperminds.tech",
        "https://trendzzo-be.xyz-app.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        *CORS_ORIGINS,  # also include anything set in .env
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(ErrorHandlerMiddleware)
app.add_middleware(MeteringMiddleware)

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
app.include_router(image_generation_router)
app.include_router(campaign_router)
app.include_router(super_admin_router)
app.include_router(social_presence_router)
app.include_router(trend_router)
app.include_router(dev_router)
app.include_router(metering_router)
app.include_router(social_publishing_router)
app.include_router(social_publishing_oauth_router)
app.include_router(social_publishing_reddit_router)
app.include_router(social_publishing_instagram_router)
app.include_router(social_publishing_facebook_router)
app.include_router(social_publishing_quora_router)
app.include_router(social_publishing_agent_router)


# ── Health Check ──────────────────────────────────────────────────────────────
@app.get("/")
def home():
    return {"message": f"{APP_NAME} v{APP_VERSION} is running", "env": APP_ENV}


@app.get("/health")
async def health_check():
    from app.database import client as mongo_client
    from datetime import datetime, timezone

    checks = {"mongodb": "unknown", "scheduler": "unknown", "ai_service": "unknown", "websockets": "unknown"}

    try:
        await mongo_client.admin.command("ping")
        checks["mongodb"] = "healthy"
    except Exception as e:
        checks["mongodb"] = f"unhealthy: {str(e)}"

    try:
        from app.services.scheduler_worker import _running as scheduler_running
        checks["scheduler"] = "running" if scheduler_running else "stopped"
    except Exception:
        checks["scheduler"] = "unknown"

    from app.config import OPENROUTER_API_KEY, USE_MOCK
    if USE_MOCK:
        checks["ai_service"] = "mock_mode"
    elif OPENROUTER_API_KEY:
        checks["ai_service"] = "configured"
    else:
        checks["ai_service"] = "not_configured"

    from app.ws.manager import ws_manager
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
    from app.ws.manager import ws_manager
    from app.services.logger import log

    channel_list = [c.strip() for c in channels.split(",") if c.strip()]
    await ws_manager.connect(websocket, user_id, channel_list)
    log.ws_event("connected", user_id=user_id, connections=ws_manager.active_connections)

    try:
        while True:
            data = await websocket.receive_text()
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
    if APP_ENV == "production":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Not available in production")

    from app.ws.manager import ws_manager
    from app.services.background_tasks import task_queue
    from app.services.feature_flags import get_all_flags

    return {
        "websockets": ws_manager.get_stats(),
        "task_queue": task_queue.get_stats(),
        "feature_flags": get_all_flags(),
        "environment": APP_ENV,
    }
