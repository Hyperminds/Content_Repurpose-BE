"""
Centralized environment configuration for TrendZZo.

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
API_PREFIX: str = ""

# ── Environment flags ─────────────────────────────────────────────────────────
IS_DEVELOPMENT = APP_ENV == "development"
IS_STAGING     = APP_ENV == "staging"
IS_PRODUCTION  = APP_ENV == "production"

# USE_MOCK_DATA controls whether AI/trend/social calls use mock data.
# Env provides the DEFAULT; the runtime value is persisted to a small state
# file so the dev toggle survives uvicorn --reload and process restarts.
_ENV_USE_MOCK: bool = os.getenv("USE_MOCK_DATA", "true" if IS_DEVELOPMENT else "false").lower() == "true"

# Path for the persisted runtime override (next to this config file).
_MOCK_STATE_FILE = Path(__file__).resolve().parent / ".mockstate"


def get_use_mock() -> bool:
    """
    Return the current mock-data flag. Reads the persisted runtime override if
    present (survives --reload/restart); otherwise falls back to the env default.
    """
    try:
        if _MOCK_STATE_FILE.exists():
            raw = _MOCK_STATE_FILE.read_text(encoding="utf-8").strip().lower()
            if raw in ("true", "1", "on"):
                return True
            if raw in ("false", "0", "off"):
                return False
    except Exception:
        pass
    return _ENV_USE_MOCK


def set_use_mock(value: bool) -> bool:
    """Persist the runtime mock-data flag. Returns the new value."""
    val = bool(value)
    try:
        _MOCK_STATE_FILE.write_text("true" if val else "false", encoding="utf-8")
    except Exception:
        pass
    return val


# Backwards-compat: modules that still read the USE_MOCK snapshot get the live
# value at import time. Prefer get_use_mock() for anything runtime-sensitive.
USE_MOCK: bool = get_use_mock()

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

# Image generation reuses the SAME OpenRouter API key/base URL above (no second
# key). Only the default MODEL is separately configurable, since text and image
# models differ. Change this env var to swap the image model without code edits.
OPENROUTER_IMAGE_MODEL: str = os.getenv(
    "OPENROUTER_IMAGE_MODEL", "google/gemini-2.5-flash-image"
)
# When true, /generate auto-creates a REAL platform-specific AI image per
# platform (parallel) instead of stock placeholders. Costs real credits per
# image, so it is config-gated; disable to fall back to deterministic stock
# images without code changes. Default ON.
ENABLE_AI_IMAGE_GENERATION: bool = os.getenv(
    "ENABLE_AI_IMAGE_GENERATION", "true"
).strip().lower() == "true"

# ── CORS ──────────────────────────────────────────────────────────────────────
_cors_raw = os.getenv("CORS_ORIGINS", "*")
CORS_ORIGINS: list = ["*"] if _cors_raw.strip() == "*" else [o.strip() for o in _cors_raw.split(",")]
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


# ── Hybrid publishing feature flags ───────────────────────────────────────────
# All default FALSE. Experimental providers are never active in production
# unless explicitly enabled. The native-API path (LinkedIn/Meta/X) is unaffected
# by these flags and always available.
def _bool_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() == "true"


# Enable the Hermes external browser-agent provider (fake adapter only today).
ENABLE_HERMES_PROVIDER: bool = _bool_env("ENABLE_HERMES_PROVIDER", False)
# Enable user-assisted Hermes mode (manual login/MFA/confirmation flows).
ENABLE_HERMES_USER_ASSISTED: bool = _bool_env("ENABLE_HERMES_USER_ASSISTED", False)
# Enable the Instagram user-assisted Hermes workflow (single-image, browser).
# Independent of the native Meta/Instagram API path, which is always available.
# Default OFF; requires ENABLE_HERMES_PROVIDER too.
ENABLE_HERMES_INSTAGRAM: bool = _bool_env("ENABLE_HERMES_INSTAGRAM", False)
# Enable the Facebook user-assisted Hermes workflow (profile OR page, browser).
# Supports text-only and single-image+text posts. Independent of the native Meta
# Graph API path, which is always available. Default OFF; requires
# ENABLE_HERMES_PROVIDER too.
ENABLE_HERMES_FACEBOOK: bool = _bool_env("ENABLE_HERMES_FACEBOOK", False)
# Enable the Quora user-assisted Hermes workflow (profile/space Post: text +
# optional single image, browser). There is NO native Quora API path. Default
# OFF; requires ENABLE_HERMES_PROVIDER too.
ENABLE_HERMES_QUORA: bool = _bool_env("ENABLE_HERMES_QUORA", False)
# Enable Bring-Your-Own-Credentials providers.
ENABLE_BYOK_PROVIDERS: bool = _bool_env("ENABLE_BYOK_PROVIDERS", False)


def get_env() -> str:
    return APP_ENV


def log_env():
    mode_label = {
        "development": "🟡 DEVELOPMENT",
        "staging":     "🟠 STAGING",
        "production":  "🟢 PRODUCTION",
    }.get(APP_ENV, f"❓ UNKNOWN ({APP_ENV})")
    mock_label = "🎭 MOCK DATA (no AI credits consumed)" if get_use_mock() else "🤖 REAL AI APIs"
    print(f"[{APP_NAME} v{APP_VERSION}] Environment: {mode_label} | AI: {mock_label}")
