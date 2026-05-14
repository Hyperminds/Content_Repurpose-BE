from contextlib import asynccontextmanager
from fastapi import FastAPI  # type:ignore
from fastapi.middleware.cors import CORSMiddleware  # type:ignore
from app.routes.content_routes import router as content_router
from app.routes.bookmark_routes import router as bookmark_router
from app.routes.history_routes import router as history_router
from app.routes.auth_routes import router as auth_router
from app.routes.scheduled_post_routes import router as scheduled_post_router
from app.database import init_db
from app.models.user_model import init_users_collection


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create indexes
    await init_db()
    await init_users_collection()
    print("✓ MongoDB connected & indexes created")
    yield
    # Shutdown


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
app.include_router(content_router)
app.include_router(bookmark_router)
app.include_router(history_router)
app.include_router(scheduled_post_router)


@app.get("/")
def home():
    return {"message": "Backend is running"}
