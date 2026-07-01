"""
Shared OpenRouter AI client.

Previously, eight separate services each constructed their own
`AsyncOpenAI(api_key=..., base_url=...)`. That is duplicated infrastructure
wiring: every service re-read the env and opened its own client/connection pool.

This module exposes ONE shared async client, configured from the centralized
`app.config` values. Services import `ai_client` and use it exactly as before
(`ai_client.chat.completions.create(...)`), so prompts, models, and all
business logic remain untouched and API contracts are identical.
"""

from openai import AsyncOpenAI
from app.config import OPENROUTER_API_KEY, AI_BASE_URL

# Single shared client reused across all AI services.
ai_client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=AI_BASE_URL,
)


def get_ai_client() -> AsyncOpenAI:
    """Accessor for the shared client (handy for DI / tests)."""
    return ai_client
