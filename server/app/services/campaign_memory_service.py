"""
Campaign Memory & User Preference Learning Service.
Tracks user preferences across generations and adapts AI output over time.
Stores learned patterns in campaign_memory collection.
"""

import json
from datetime import datetime, timezone
from openai import AsyncOpenAI
import os
from pathlib import Path
from dotenv import load_dotenv
from app.database import db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.core.ai_client import ai_client as client
MODEL = "openai/gpt-4o-mini"

campaign_memory_collection = db["campaign_memory"]


# ── Default memory structure ──────────────────────────────────────────────────

DEFAULT_MEMORY = {
    "preferred_tone": None,
    "preferred_hook_style": None,
    "emoji_preference": "moderate",   # none | minimal | moderate | heavy
    "cta_preference": None,
    "best_performing_content_types": [],
    "preferred_post_length": "medium",  # short | medium | long
    "approved_count": 0,
    "regenerated_count": 0,
    "skipped_count": 0,
    "total_generations": 0,
    "platform_preferences": {},        # platform -> {approved, skipped, regenerated}
    "pillar_performance": {},          # pillar -> {approved, skipped}
    "chat_modifications": [],          # last 10 chat prompts used
    "ai_learned_insights": [],         # AI-generated insights about user style
    "last_updated": None,
}


# ── Core Memory Operations ────────────────────────────────────────────────────

async def get_memory(user_id: str) -> dict:
    """Get or create user memory."""
    doc = await campaign_memory_collection.find_one({"user_id": user_id})
    if not doc:
        memory = {"user_id": user_id, **DEFAULT_MEMORY, "created_at": datetime.now(timezone.utc)}
        await campaign_memory_collection.insert_one(memory)
        return serialize_memory(memory)
    return serialize_memory(doc)


async def update_memory(user_id: str, updates: dict) -> dict:
    """Update specific memory fields."""
    updates["last_updated"] = datetime.now(timezone.utc)
    await campaign_memory_collection.update_one(
        {"user_id": user_id},
        {"$set": updates},
        upsert=True,
    )
    return await get_memory(user_id)


async def record_generation(user_id: str, platform: str, content_type: str, pillar: str):
    """Record a new generation event."""
    await campaign_memory_collection.update_one(
        {"user_id": user_id},
        {
            "$inc": {"total_generations": 1, f"platform_preferences.{platform}.generated": 1},
            "$set": {"last_updated": datetime.now(timezone.utc)},
        },
        upsert=True,
    )


async def record_approval(user_id: str, platform: str, content_type: str, pillar: str, content: str):
    """Record when user approves content — learn from this."""
    await campaign_memory_collection.update_one(
        {"user_id": user_id},
        {
            "$inc": {
                "approved_count": 1,
                f"platform_preferences.{platform}.approved": 1,
                f"pillar_performance.{pillar}.approved": 1,
            },
            "$addToSet": {"best_performing_content_types": content_type},
            "$set": {"last_updated": datetime.now(timezone.utc)},
        },
        upsert=True,
    )
    # Analyze approved content to extract style preferences
    await _analyze_approved_content(user_id, content, platform)


async def record_skip(user_id: str, platform: str, pillar: str):
    """Record when user skips content."""
    await campaign_memory_collection.update_one(
        {"user_id": user_id},
        {
            "$inc": {
                "skipped_count": 1,
                f"platform_preferences.{platform}.skipped": 1,
                f"pillar_performance.{pillar}.skipped": 1,
            },
            "$set": {"last_updated": datetime.now(timezone.utc)},
        },
        upsert=True,
    )


async def record_regeneration(user_id: str, platform: str):
    """Record when user regenerates content."""
    await campaign_memory_collection.update_one(
        {"user_id": user_id},
        {
            "$inc": {"regenerated_count": 1, f"platform_preferences.{platform}.regenerated": 1},
            "$set": {"last_updated": datetime.now(timezone.utc)},
        },
        upsert=True,
    )


async def record_chat_modification(user_id: str, prompt: str):
    """Record chat modification prompts to learn user preferences."""
    await campaign_memory_collection.update_one(
        {"user_id": user_id},
        {
            "$push": {
                "chat_modifications": {
                    "$each": [{"prompt": prompt, "at": datetime.now(timezone.utc).isoformat()}],
                    "$slice": -20,  # Keep last 20
                }
            },
            "$set": {"last_updated": datetime.now(timezone.utc)},
        },
        upsert=True,
    )


async def _analyze_approved_content(user_id: str, content: str, platform: str):
    """Use AI to extract style preferences from approved content."""
    if not content or len(content) < 50:
        return

    prompt = f"""Analyze this approved {platform} post and extract style preferences.

Content: {content[:500]}

Return JSON:
{{
  "tone": "professional|casual|humorous|inspirational|educational",
  "hook_style": "question|statement|statistic|story|bold_claim",
  "emoji_usage": "none|minimal|moderate|heavy",
  "length": "short|medium|long",
  "cta_style": "direct|soft|question|none"
}}

Return ONLY valid JSON."""

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=150,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        prefs = json.loads(raw.strip())

        # Update memory with extracted preferences
        await campaign_memory_collection.update_one(
            {"user_id": user_id},
            {"$set": {
                "preferred_tone": prefs.get("tone"),
                "preferred_hook_style": prefs.get("hook_style"),
                "emoji_preference": prefs.get("emoji_usage", "moderate"),
                "preferred_post_length": prefs.get("length", "medium"),
                "cta_preference": prefs.get("cta_style"),
                "last_updated": datetime.now(timezone.utc),
            }},
        )
    except Exception as e:
        print(f"[Memory] Style analysis failed: {e}")


async def generate_ai_insights(user_id: str) -> list:
    """Generate AI insights about user's content preferences."""
    memory = await get_memory(user_id)

    if memory.get("total_generations", 0) < 3:
        return ["Generate and approve more content to unlock personalized insights."]

    prompt = f"""Based on this user's content generation history, generate 3-4 personalized insights.

Stats:
- Total generations: {memory.get('total_generations', 0)}
- Approved: {memory.get('approved_count', 0)}
- Regenerated: {memory.get('regenerated_count', 0)}
- Skipped: {memory.get('skipped_count', 0)}
- Preferred tone: {memory.get('preferred_tone', 'unknown')}
- Preferred hook: {memory.get('preferred_hook_style', 'unknown')}
- Emoji preference: {memory.get('emoji_preference', 'moderate')}
- Post length: {memory.get('preferred_post_length', 'medium')}
- Best content types: {memory.get('best_performing_content_types', [])}
- Platform preferences: {memory.get('platform_preferences', {})}

Generate 3-4 short, specific insights about their content style.
Return as JSON array of strings. Example:
["You prefer professional tone with question hooks", "LinkedIn content gets approved most often"]

Return ONLY a JSON array."""

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=200,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        insights = json.loads(raw.strip())

        # Save insights
        await campaign_memory_collection.update_one(
            {"user_id": user_id},
            {"$set": {"ai_learned_insights": insights, "last_updated": datetime.now(timezone.utc)}},
        )
        return insights
    except Exception as e:
        print(f"[Memory] Insight generation failed: {e}")
        return ["Keep generating content to unlock AI insights."]


def get_memory_context(memory: dict) -> str:
    """Build a memory context string to inject into AI prompts."""
    if not memory:
        return ""

    parts = []
    if memory.get("preferred_tone"):
        parts.append(f"User prefers {memory['preferred_tone']} tone")
    if memory.get("preferred_hook_style"):
        parts.append(f"User likes {memory['preferred_hook_style']} hooks")
    if memory.get("emoji_preference") and memory["emoji_preference"] != "moderate":
        parts.append(f"User prefers {memory['emoji_preference']} emoji usage")
    if memory.get("preferred_post_length"):
        parts.append(f"User prefers {memory['preferred_post_length']} posts")
    if memory.get("cta_preference"):
        parts.append(f"User prefers {memory['cta_preference']} CTAs")
    if memory.get("best_performing_content_types"):
        types = ", ".join(memory["best_performing_content_types"][:3])
        parts.append(f"Best performing content types: {types}")

    if not parts:
        return ""

    return "USER STYLE MEMORY (adapt to these preferences):\n" + "\n".join(f"- {p}" for p in parts)


def serialize_memory(doc: dict) -> dict:
    return {
        "user_id": doc.get("user_id"),
        "preferred_tone": doc.get("preferred_tone"),
        "preferred_hook_style": doc.get("preferred_hook_style"),
        "emoji_preference": doc.get("emoji_preference", "moderate"),
        "cta_preference": doc.get("cta_preference"),
        "best_performing_content_types": doc.get("best_performing_content_types", []),
        "preferred_post_length": doc.get("preferred_post_length", "medium"),
        "approved_count": doc.get("approved_count", 0),
        "regenerated_count": doc.get("regenerated_count", 0),
        "skipped_count": doc.get("skipped_count", 0),
        "total_generations": doc.get("total_generations", 0),
        "platform_preferences": doc.get("platform_preferences", {}),
        "pillar_performance": doc.get("pillar_performance", {}),
        "chat_modifications": doc.get("chat_modifications", []),
        "ai_learned_insights": doc.get("ai_learned_insights", []),
        "last_updated": doc.get("last_updated").isoformat() if doc.get("last_updated") else None,
    }
