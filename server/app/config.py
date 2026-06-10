"""
Centralized environment configuration for TrendZo.
Uses Pydantic BaseSettings for type-safe, validated configuration.

APP_ENV values:
  development  — mock data only, zero API credits consumed
  staging      — partial real APIs, integration testing
  production   — fully real APIs, real AI, real publishing
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env before anything else
load_dotenv(Path(__file__).resolve().parent / ".env")

# ── Core settings ─────────────────────────────────────────────────────────────
APP_ENV: str = os.getenv("APP_ENV", "development").lower()
APP_NAME: str = "TrendZZo"
APP_VERSION: str = "1.0.0"
API_PREFIX: str = ""  # Set to "/api/v1" when ready for versioning

# ── Environment flags ─────────────────────────────────────────────────────────
IS_DEVELOPMENT = APP_ENV == "development"
IS_STAGING     = APP_ENV == "staging"
IS_PRODUCTION  = APP_ENV == "production"

# USE_MOCK_DATA controls whether AI/trend/social calls use mock data.
# Decoupled from APP_ENV so you can deploy in "production" mode
# (real CORS, real auth, real DB) while still saving AI credits.
# Set USE_MOCK_DATA=true in Render env vars to keep mock AI responses.
USE_MOCK: bool = os.getenv("USE_MOCK_DATA", "true" if IS_DEVELOPMENT else "false").lower() == "true"

# ── Database ──────────────────────────────────────────────────────────────────
MONGODB_URL: str = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DB_NAME: str = os.getenv("DB_NAME", "content_repurposer")

# ── Auth ──────────────────────────────────────────────────────────────────────
JWT_SECRET: str = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRY_MINUTES: int = int(os.getenv("JWT_EXPIRY_MINUTES", "43200"))

# ── AI / OpenRouter ───────────────────────────────────────────────────────────
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
AI_MODEL: str = os.getenv("AI_MODEL", "openrouter/free")
AI_BASE_URL: str = "https://openrouter.ai/api/v1"

# ── CORS ──────────────────────────────────────────────────────────────────────
_cors_raw = os.getenv("CORS_ORIGINS", "*")
CORS_ORIGINS: list = ["*"] if _cors_raw.strip() == "*" else [o.strip() for o in _cors_raw.split(",")]
# When allowing all origins, credentials must be disabled (browser security rule)
CORS_ALLOW_CREDENTIALS: bool = CORS_ORIGINS != ["*"]

# ── SMTP ──────────────────────────────────────────────────────────────────────
SMTP_EMAIL: str = os.getenv("SMTP_EMAIL", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")

# ── LinkedIn OAuth ────────────────────────────────────────────────────────────
LINKEDIN_CLIENT_ID: str = os.getenv("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET: str = os.getenv("LINKEDIN_CLIENT_SECRET", "")
LINKEDIN_REDIRECT_URI: str = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/auth/linkedin/callback")

# ── Frontend ──────────────────────────────────────────────────────────────────
FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")


def get_env() -> str:
    return APP_ENV


def log_env():
    mode_label = {
        "development": "🟡 DEVELOPMENT",
        "staging":     "🟠 STAGING",
        "production":  "🟢 PRODUCTION",
    }.get(APP_ENV, f"❓ UNKNOWN ({APP_ENV})")
    mock_label = "🎭 MOCK DATA (no AI credits consumed)" if USE_MOCK else "🤖 REAL AI APIs"
    print(f"[{APP_NAME} v{APP_VERSION}] Environment: {mode_label} | AI: {mock_label}")
