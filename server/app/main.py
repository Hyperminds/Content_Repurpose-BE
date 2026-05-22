from contextlib import asynccontextmanager
from fastapi import FastAPI  # type:ignore
from fastapi.middleware.cors import CORSMiddleware  # type:ignore
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
from app.routes.super_admin_routes import router as super_admin_router
from app.database import init_db
from app.models.user_model import init_users_collection
from app.services.scheduler_worker import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create indexes and start scheduler
    await init_db()
    await init_users_collection()
    start_scheduler()
    print("✓ MongoDB connected & indexes created")
    print("✓ Scheduler worker started")
    yield
    # Shutdown
    stop_scheduler()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
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
app.include_router(super_admin_router)


@app.get("/")
def home():
    return {"message": "Backend is running"}
