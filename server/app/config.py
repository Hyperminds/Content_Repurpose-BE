"""
Centralized environment configuration for TrendZo.
Controls whether the app uses real APIs or mock data.

APP_ENV values:
  development  — mock data only, zero API credits consumed
  staging      — partial real APIs, integration testing
  production   — fully real APIs, real AI, real publishing
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

APP_ENV: str = os.getenv("APP_ENV", "development").lower()

IS_DEVELOPMENT = APP_ENV == "development"
IS_STAGING     = APP_ENV == "staging"
IS_PRODUCTION  = APP_ENV == "production"

# Convenience: should we use mock data?
USE_MOCK = IS_DEVELOPMENT


def get_env() -> str:
    return APP_ENV


def log_env():
    mode_label = {
        "development": "🟡 DEVELOPMENT (mock data — no API credits consumed)",
        "staging":     "🟠 STAGING (partial real APIs)",
        "production":  "🟢 PRODUCTION (fully real APIs)",
    }.get(APP_ENV, f"❓ UNKNOWN ({APP_ENV})")
    print(f"[TrendZo] Environment: {mode_label}")
