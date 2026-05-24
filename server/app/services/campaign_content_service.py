"""
Campaign Day Content Generation Service.
Generates platform-specific content for each campaign day,
tied to campaign context, pillar, purpose, and target emotion.
"""

import json
from datetime import datetime, timezone
from bson import ObjectId
from openai import AsyncOpenAI
import os
from pathlib import Path
from dotenv import load_dotenv
from app.database import db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = AsyncOpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)
MODEL = "openrouter/free"

campaign_days_collection    = db["campaign_days"]
campaign_content_collection = db["campaign_content"]

PLATFORM_CHAR_LIMITS = {
    "linkedin": 3000, "twitter": 220, "instagram": 2200,
    "reddit": 40000, "medium": 99999, "meta": 63206, "quora": 99999,
}

PLATFORM_RULES = {
    "linkedin":  "Professional tone. No markdown. Short paragraphs. End with 5-8 relevant hashtags.",
    "twitter":   "Under 220 characters. Punchy. No markdown. 1-2 hashtags max. No 'TWEET:' prefix.",
    "instagram": "Engaging caption. Emoji-friendly. End with 15-20 hashtags.",
    "reddit":    "Conversational. No hashtags. Authentic. Suggest 2 subreddits at end.",
    "medium":    "Article excerpt. Strong hook. Storytelling. End with 3-5 tags.",
    "meta":      "Conversational. Question-driven. 2-4 hashtags.",
    "quora":     "Expert answer format. First-person. No hashtags. Authoritative.",
}

ENGAGEMENT_PREDICTIONS = {
    "Curiosity":    {"level": "High",   "reason": "Curiosity-driven content consistently outperforms average by 2-3x"},
    "Inspiration":  {"level": "High",   "reason": "Inspirational content drives saves and shares"},
    "Trust":        {"level": "Medium", "reason": "Trust-building content generates quality comments"},
    "Urgency":      {"level": "Medium", "reason": "Urgency triggers immediate action but lower long-term engagement"},
    "FOMO":         {"level": "High",   "reason": "FOMO content drives rapid sharing and profile visits"},
    "Empathy":      {"level": "Medium", "reason": "Empathetic content builds loyal audience relationships"},
    "Excitement":   {"level": "High",   "reason": "Excitement generates viral potential and rapid spread"},
    "Validation":   {"level": "Medium", "reason": "Validation content drives comments and community discussion"},
}


async def generate_day_content(day: dict, campaign: dict) -> dict:
    """
    Generate AI content for a specific campaign day.
    Content is fully tied to campaign context.
    """
    platform     = day.get("platform", "linkedin")
    content_type = day.get("content_type", "Post")
    purpose      = day.get("purpose", "Awareness")
    emotion      = day.get("target_emotion", "Curiosity")
    pillar       = day.get("content_pillar", "Core Content")
    cta          = day.get("cta", campaign.get("cta_goal", ""))
    char_limit   = PLATFORM_CHAR_LIMITS.get(platform, 3000)
    rules        = PLATFORM_RULES.get(platform, "")

    prompt = f"""
You are a campaign content strategist creating content for a specific campaign day.

CAMPAIGN CONTEXT:
- Campaign: {campaign['campaign_name']}
- Goal: {campaign['campaign_goal']}
- Type: {campaign['campaign_type']}
- Target Audience: {campaign['target_audience']}
- Tone: {campaign['tone']}
- CTA Goal: {cta}

DAY CONTEXT:
- Platform: {platform}
- Content Type: {content_type}
- Content Pillar: {pillar}
- Purpose: {purpose}
- Target Emotion: {emotion}
- Day: {day.get('day_number', 1)} of {campaign.get('duration', 30)}
- Week Theme: {day.get('week_theme', 'Content Week')}

PLATFORM RULES: {rules}
CHARACTER LIMIT: {char_limit}

Generate a JSON response with EXACTLY this structure:
{{
  "content": "The actual post content following platform rules and character limits",
  "content_hook": "The opening line/hook of the content (first sentence)",
  "platform_reason": "1-2 sentences explaining why {platform} is the right platform for this specific message on this day",
  "purpose_explanation": "1-2 sentences explaining what this post achieves in the campaign journey",
  "engagement_prediction": "1-2 sentences predicting how the audience will respond and why",
  "optimization_tips": ["tip 1", "tip 2", "tip 3"],
  "best_posting_time": "Recommended time to post (e.g. 9:00 AM)"
}}

IMPORTANT: 
- Content must be campaign-specific, NOT generic
- Reference the campaign goal and audience naturally
- Trigger the {emotion} emotion authentically
- Stay within {char_limit} characters for the content field
- Return ONLY valid JSON, no markdown
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.75,
        max_tokens=800,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    data = json.loads(raw)

    # Get engagement prediction
    eng = ENGAGEMENT_PREDICTIONS.get(emotion, {"level": "Medium", "reason": "Solid engagement expected"})

    return {
        "content": data.get("content", ""),
        "content_hook": data.get("content_hook", ""),
        "platform_reason": data.get("platform_reason", ""),
        "purpose_explanation": data.get("purpose_explanation", ""),
        "engagement_prediction": data.get("engagement_prediction", ""),
        "engagement_level": eng["level"],
        "engagement_reason": eng["reason"],
        "optimization_tips": data.get("optimization_tips", []),
        "best_posting_time": data.get("best_posting_time", ""),
    }


async def save_day_content(day_id: str, campaign_id: str, user_id: str, content_data: dict) -> dict:
    """Save or update generated content for a campaign day."""
    now = datetime.now(timezone.utc)

    doc = {
        "day_id": day_id,
        "campaign_id": campaign_id,
        "user_id": user_id,
        **content_data,
        "status": "draft",
        "generated_at": now,
        "updated_at": now,
    }

    result = await campaign_content_collection.update_one(
        {"day_id": day_id},
        {"$set": doc},
        upsert=True,
    )

    saved = await campaign_content_collection.find_one({"day_id": day_id})
    return serialize_content(saved)


async def get_day_content(day_id: str) -> dict:
    """Get content for a specific day."""
    doc = await campaign_content_collection.find_one({"day_id": day_id})
    if not doc:
        return None
    return serialize_content(doc)


async def update_day_content(day_id: str, user_id: str, updates: dict) -> dict:
    """Update content fields (edit, status change, platform change)."""
    updates["updated_at"] = datetime.now(timezone.utc)
    await campaign_content_collection.update_one(
        {"day_id": day_id, "user_id": user_id},
        {"$set": updates},
    )
    doc = await campaign_content_collection.find_one({"day_id": day_id})
    return serialize_content(doc) if doc else None


async def update_day_status(day_id: str, user_id: str, status: str) -> dict:
    """Update the status of a campaign day."""
    # Update in campaign_days
    await campaign_days_collection.update_one(
        {"_id": ObjectId(day_id)},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}},
    )
    # Also update in content if exists
    await campaign_content_collection.update_one(
        {"day_id": day_id},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}},
    )
    doc = await campaign_days_collection.find_one({"_id": ObjectId(day_id)})
    return serialize_day(doc) if doc else None


def serialize_content(doc: dict) -> dict:
    if not doc:
        return None
    return {
        "id": str(doc["_id"]),
        "day_id": doc.get("day_id"),
        "campaign_id": doc.get("campaign_id"),
        "content": doc.get("content", ""),
        "content_hook": doc.get("content_hook", ""),
        "platform_reason": doc.get("platform_reason", ""),
        "purpose_explanation": doc.get("purpose_explanation", ""),
        "engagement_prediction": doc.get("engagement_prediction", ""),
        "engagement_level": doc.get("engagement_level", "Medium"),
        "engagement_reason": doc.get("engagement_reason", ""),
        "optimization_tips": doc.get("optimization_tips", []),
        "best_posting_time": doc.get("best_posting_time", ""),
        "status": doc.get("status", "draft"),
        "generated_at": doc.get("generated_at").isoformat() if doc.get("generated_at") else None,
        "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
    }


def serialize_day(doc: dict) -> dict:
    if not doc:
        return None
    return {
        "id": str(doc["_id"]),
        "campaign_id": doc.get("campaign_id"),
        "day_number": doc.get("day_number"),
        "date": doc.get("date"),
        "week_number": doc.get("week_number"),
        "week_theme": doc.get("week_theme"),
        "platform": doc.get("platform"),
        "content_type": doc.get("content_type"),
        "purpose": doc.get("purpose"),
        "target_emotion": doc.get("target_emotion"),
        "cta": doc.get("cta"),
        "content_pillar": doc.get("content_pillar"),
        "ai_reasoning": doc.get("ai_reasoning"),
        "status": doc.get("status", "planned"),
    }
