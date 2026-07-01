"""
Campaign AI Strategy Generation Service.
Uses OpenAI (via OpenRouter) to generate complete campaign blueprints,
weekly structures, content pillars, and day-by-day content plans.
"""

import os
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from openai import AsyncOpenAI
from app.database import db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.core.ai_client import ai_client as client
MODEL = "openai/gpt-4o-mini"

campaign_days_collection    = db["campaign_days"]
campaign_content_collection = db["campaign_content"]


# ── Content type pools per platform ──────────────────────────────────────────
PLATFORM_CONTENT_TYPES = {
    "linkedin":  ["Thought Leadership", "Case Study", "Industry Insight", "Personal Story", "How-To Guide", "Poll", "Carousel", "Article"],
    "twitter":   ["Viral Tweet", "Thread", "Hot Take", "Quick Tip", "Question", "Announcement", "Engagement Bait"],
    "instagram": ["Carousel", "Reel", "Story", "Quote Card", "Behind the Scenes", "Product Showcase", "User Story"],
    "reddit":    ["Discussion Post", "AMA", "Resource Share", "Opinion Post", "Tutorial", "Community Question"],
    "medium":    ["Long-form Article", "Tutorial", "Opinion Piece", "Case Study", "Industry Analysis"],
    "meta":      ["Engagement Post", "Video", "Story", "Event Promotion", "Community Post", "Poll"],
    "quora":     ["Expert Answer", "Question Post", "Knowledge Share", "Opinion Answer"],
}

CONTENT_PILLARS = {
    "brand_awareness":    ["Brand Story", "Values & Mission", "Social Proof", "Behind the Scenes", "Team Spotlight"],
    "lead_generation":    ["Problem Awareness", "Solution Showcase", "Social Proof", "CTA Content", "Free Value"],
    "product_launch":     ["Teaser", "Feature Reveal", "Use Case", "Testimonial", "Launch Announcement"],
    "thought_leadership": ["Industry Insight", "Contrarian View", "Trend Analysis", "Expert Opinion", "Research Share"],
    "community_building": ["Community Story", "User Spotlight", "Discussion Starter", "Value Share", "Celebration"],
    "event_promotion":    ["Event Teaser", "Speaker Spotlight", "Agenda Preview", "Registration CTA", "Countdown"],
    "content_series":     ["Series Intro", "Episode Content", "Recap", "Behind the Series", "Audience Q&A"],
}

EMOTIONS = ["Curiosity", "Inspiration", "Trust", "Urgency", "FOMO", "Empathy", "Excitement", "Validation"]


async def generate_campaign_strategy(campaign: dict) -> dict:
    """
    Generate a complete AI campaign strategy.
    Returns blueprint, pillars, content mix, and weekly structure.
    """
    prompt = f"""
You are an expert content strategist. Generate a comprehensive campaign strategy for:

Campaign: {campaign['campaign_name']}
Goal: {campaign['campaign_goal']}
Type: {campaign['campaign_type']}
Target Audience: {campaign['target_audience']}
Duration: {campaign['duration']} days
Platforms: {', '.join(campaign['selected_platforms'])}
Posting Frequency: {campaign['posting_frequency']}
Tone: {campaign['tone']}
CTA Goal: {campaign['cta_goal']}

Return a JSON object with EXACTLY this structure:
{{
  "campaign_blueprint": "2-3 sentence strategic overview of the campaign approach",
  "campaign_objective": "Single clear measurable objective",
  "content_pillars": [
    {{"name": "Pillar Name", "description": "What this pillar achieves", "percentage": 25}},
    {{"name": "Pillar Name", "description": "What this pillar achieves", "percentage": 25}},
    {{"name": "Pillar Name", "description": "What this pillar achieves", "percentage": 25}},
    {{"name": "Pillar Name", "description": "What this pillar achieves", "percentage": 25}}
  ],
  "content_mix": {{
    "educational": 30,
    "promotional": 20,
    "engagement": 25,
    "storytelling": 25
  }},
  "platform_strategy": {{
    "platform_name": "Strategy for this platform in 1 sentence"
  }},
  "weekly_themes": [
    "Week 1 theme",
    "Week 2 theme",
    "Week 3 theme",
    "Week 4 theme"
  ],
  "key_messages": [
    "Core message 1",
    "Core message 2",
    "Core message 3"
  ],
  "success_metrics": [
    "Metric 1",
    "Metric 2",
    "Metric 3"
  ]
}}

Return ONLY valid JSON. No markdown, no explanation.
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=1200,
    )

    raw = response.choices[0].message.content.strip()
    # Strip markdown code blocks if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)


async def generate_campaign_days(campaign: dict, strategy: dict) -> list:
    """
    Generate day-by-day content plan for the campaign.
    Creates campaign_days documents in MongoDB.
    """
    campaign_id = campaign["id"]
    duration    = min(campaign["duration"], 90)  # Cap at 90 days for performance
    platforms   = campaign["selected_platforms"]
    start_date  = campaign.get("start_date", datetime.now().strftime("%Y-%m-%d"))
    pillars     = [p["name"] for p in strategy.get("content_pillars", [])]
    weekly_themes = strategy.get("weekly_themes", ["Build Awareness", "Drive Engagement", "Convert", "Retain"])

    # Determine posts per day based on frequency
    freq_map = {
        "daily": 1, "twice_daily": 2, "every_other_day": 0.5,
        "weekly": 1/7, "custom": 1,
    }
    posts_per_day = freq_map.get(campaign.get("posting_frequency", "daily"), 1)

    # Delete existing days for this campaign
    await campaign_days_collection.delete_many({"campaign_id": campaign_id})

    days = []
    start_dt = datetime.fromisoformat(start_date)

    for day_num in range(1, duration + 1):
        # Skip days based on frequency
        if posts_per_day < 1 and day_num % int(1 / posts_per_day) != 0:
            continue

        current_date = start_dt + timedelta(days=day_num - 1)
        week_num = min((day_num - 1) // 7, len(weekly_themes) - 1)
        week_theme = weekly_themes[week_num] if weekly_themes else "Content Week"

        # Rotate platforms
        platform = platforms[(day_num - 1) % len(platforms)]
        content_types = PLATFORM_CONTENT_TYPES.get(platform, ["Post"])
        content_type = content_types[(day_num - 1) % len(content_types)]

        # Rotate pillars
        pillar = pillars[(day_num - 1) % len(pillars)] if pillars else "Core Content"

        # Rotate emotions
        emotion = EMOTIONS[(day_num - 1) % len(EMOTIONS)]

        # Determine purpose based on week
        if week_num == 0:
            purpose = "Awareness & Introduction"
        elif week_num == 1:
            purpose = "Engagement & Trust Building"
        elif week_num == 2:
            purpose = "Conversion & Action"
        else:
            purpose = "Retention & Loyalty"

        # AI reasoning
        reasoning = f"Day {day_num} focuses on {pillar} to {purpose.lower()}. Using {content_type} on {platform} to trigger {emotion} in {campaign['target_audience']}."

        doc = {
            "campaign_id": campaign_id,
            "user_id": campaign["user_id"],
            "day_number": day_num,
            "date": current_date.strftime("%Y-%m-%d"),
            "week_number": week_num + 1,
            "week_theme": week_theme,
            "platform": platform,
            "content_type": content_type,
            "purpose": purpose,
            "target_emotion": emotion,
            "cta": campaign.get("cta_goal", ""),
            "content_pillar": pillar,
            "ai_reasoning": reasoning,
            "status": "planned",
            "created_at": datetime.now(timezone.utc),
        }
        days.append(doc)

    if days:
        await campaign_days_collection.insert_many(days)

    return days


def serialize_day(doc: dict) -> dict:
    return {
        "id": str(doc.get("_id", "")),
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


async def get_campaign_days(campaign_id: str, week: int = None) -> list:
    """Get campaign days, optionally filtered by week."""
    query = {"campaign_id": campaign_id}
    if week:
        query["week_number"] = week
    cursor = campaign_days_collection.find(query).sort("day_number", 1)
    docs = await cursor.to_list(length=500)
    return [serialize_day(doc) for doc in docs]


async def get_campaign_weeks(campaign_id: str) -> list:
    """Get weekly summary for a campaign."""
    pipeline = [
        {"$match": {"campaign_id": campaign_id}},
        {"$group": {
            "_id": "$week_number",
            "week_theme": {"$first": "$week_theme"},
            "days": {"$sum": 1},
            "platforms": {"$addToSet": "$platform"},
            "content_types": {"$addToSet": "$content_type"},
        }},
        {"$sort": {"_id": 1}},
    ]
    results = await campaign_days_collection.aggregate(pipeline).to_list(length=20)
    return [
        {
            "week_number": r["_id"],
            "week_theme": r.get("week_theme", f"Week {r['_id']}"),
            "days": r["days"],
            "platforms": r["platforms"],
            "content_types": r["content_types"],
        }
        for r in results
    ]
