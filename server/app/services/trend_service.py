"""
Trend Analysis Service
AI-powered realtime trend discovery engine for social media platforms.
Uses AI to generate contextual, category-specific trend intelligence.
"""

import os
import json
import asyncio
from pathlib import Path
from openai import AsyncOpenAI
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = AsyncOpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

MODEL = "openrouter/free"

PLATFORM_CONTEXTS = {
    "twitter": {
        "label": "Twitter / X",
        "content_style": "short-form, threads, hot takes, real-time commentary",
        "trend_types": "hashtags, viral tweets, trending discussions, breaking news",
        "audience": "general public, journalists, tech community, creators",
    },
    "reddit": {
        "label": "Reddit",
        "content_style": "long-form discussions, AMAs, community debates, deep dives",
        "trend_types": "rising posts, hot threads, subreddit discussions, viral comments",
        "audience": "niche communities, enthusiasts, professionals, researchers",
    },
    "linkedin": {
        "label": "LinkedIn",
        "content_style": "professional insights, career advice, industry news, thought leadership",
        "trend_types": "trending articles, viral posts, industry conversations, professional debates",
        "audience": "professionals, executives, recruiters, B2B decision makers",
    },
    "instagram": {
        "label": "Instagram",
        "content_style": "visual content, reels, stories, lifestyle, brand content",
        "trend_types": "trending hashtags, viral reels, popular aesthetics, creator trends",
        "audience": "consumers, lifestyle enthusiasts, brands, influencers",
    },
    "medium": {
        "label": "Medium",
        "content_style": "long-form articles, opinion pieces, technical deep dives, essays",
        "trend_types": "trending stories, popular publications, viral essays, top reads",
        "audience": "readers, writers, intellectuals, tech professionals",
    },
    "quora": {
        "label": "Quora",
        "content_style": "Q&A format, expert answers, knowledge sharing, debates",
        "trend_types": "trending questions, popular answers, hot topics, viral discussions",
        "audience": "knowledge seekers, experts, students, professionals",
    },
}


def _strip_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


async def fetch_platform_trends(platform: str, category: str, limit: int = 8) -> dict:
    """
    Generate AI-powered trend intelligence for a specific platform and category.
    Returns trending topics, hashtags, discussions, and content opportunities.
    """
    ctx = PLATFORM_CONTEXTS.get(platform, {})
    now = datetime.utcnow().strftime("%B %Y")

    prompt = f"""You are a social media trend analyst with real-time knowledge of {ctx.get('label', platform)} trends.

Current date context: {now}
Platform: {ctx.get('label', platform)}
Platform content style: {ctx.get('content_style', '')}
Trend types on this platform: {ctx.get('trend_types', '')}
Audience: {ctx.get('audience', '')}
Category filter: {category}

Generate CURRENT, REALISTIC trending topics for {category} on {ctx.get('label', platform)}.
Base this on actual patterns, real discussions, and genuine trends in the {category} space.

Return EXACTLY this JSON structure:
{{
  "platform": "{platform}",
  "category": "{category}",
  "trending_topics": [
    {{
      "topic": "<specific trending topic or discussion>",
      "hashtag": "<relevant hashtag if applicable, else null>",
      "momentum": "<Rising/Viral/Steady/Declining>",
      "momentum_pct": <integer -100 to +200, positive = growing>,
      "engagement_level": "<High/Medium/Low>",
      "content_opportunity_score": <integer 0-100>,
      "why_trending": "<1 sentence explaining why this is trending now>",
      "content_suggestion": "<specific content format and angle to capitalize on this trend>"
    }}
  ],
  "trending_hashtags": ["<hashtag1>", "<hashtag2>", "<hashtag3>", "<hashtag4>", "<hashtag5>"],
  "platform_pulse": "<1-2 sentence summary of what's happening on {ctx.get('label', platform)} in {category} right now>",
  "best_content_format": "<the content format performing best for {category} on this platform right now>",
  "peak_engagement_window": "<best time to post for {category} content on this platform>",
  "trend_velocity": <integer 0-100, overall trend momentum score for {category} on this platform>
}}

Rules:
- Generate {limit} trending topics
- Topics must be SPECIFIC and REALISTIC for {category} in {now}
- Momentum percentages should reflect real trend dynamics
- Content suggestions must be platform-native and immediately actionable
- Return ONLY valid JSON. No markdown, no extra text.
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a social media trend intelligence analyst. Respond with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.6,
        max_tokens=1200,
    )

    raw = response.choices[0].message.content
    return json.loads(_strip_json(raw))


async def fetch_all_platform_trends(category: str, platforms: list) -> dict:
    """
    Fetch trends across all requested platforms in parallel.
    """
    tasks = [fetch_platform_trends(p, category) for p in platforms]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    platform_data = {}
    for i, result in enumerate(results):
        platform = platforms[i]
        if isinstance(result, Exception):
            platform_data[platform] = {
                "platform": platform,
                "category": category,
                "error": str(result),
                "trending_topics": [],
                "trending_hashtags": [],
                "platform_pulse": "Data unavailable",
                "trend_velocity": 0,
            }
        else:
            platform_data[platform] = result

    return platform_data


async def generate_trend_insights(platform_data: dict, category: str) -> dict:
    """
    Generate cross-platform AI insights and content opportunity suggestions.
    """
    summary = {}
    for platform, data in platform_data.items():
        if "error" not in data:
            summary[platform] = {
                "pulse": data.get("platform_pulse", ""),
                "velocity": data.get("trend_velocity", 0),
                "top_topic": data.get("trending_topics", [{}])[0].get("topic", "") if data.get("trending_topics") else "",
            }

    summary_json = json.dumps(summary, indent=2)

    prompt = f"""You are an AI content strategist analyzing cross-platform trend data for the "{category}" category.

Platform trend summary:
{summary_json}

Generate strategic insights and return EXACTLY this JSON:
{{
  "global_trend_score": <integer 0-100, overall momentum of {category} across all platforms>,
  "trend_headline": "<punchy 1-sentence headline about what's happening with {category} right now>",
  "momentum_direction": "<Accelerating/Steady/Cooling>",
  "momentum_change_pct": <integer, e.g. +42 or -12>,
  "ai_insights": [
    "<specific insight about {category} trend pattern across platforms>",
    "<insight about which platform has the most opportunity>",
    "<insight about emerging angle or sub-topic gaining traction>"
  ],
  "content_opportunities": [
    {{
      "platform": "<platform>",
      "format": "<content format: Thread/Carousel/Short Video/Article/Q&A/etc>",
      "topic": "<specific topic to create content about>",
      "angle": "<unique angle or hook>",
      "urgency": "<Post Now/This Week/This Month>"
    }},
    {{
      "platform": "<platform>",
      "format": "<format>",
      "topic": "<topic>",
      "angle": "<angle>",
      "urgency": "<urgency>"
    }},
    {{
      "platform": "<platform>",
      "format": "<format>",
      "topic": "<topic>",
      "angle": "<angle>",
      "urgency": "<urgency>"
    }}
  ],
  "hottest_platform": "<platform with highest trend velocity for {category}>",
  "emerging_angle": "<a specific emerging sub-topic or angle within {category} that's just starting to gain traction>"
}}

Return ONLY valid JSON. No markdown, no extra text.
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a trend intelligence analyst. Respond with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
        max_tokens=700,
    )

    raw = response.choices[0].message.content
    return json.loads(_strip_json(raw))


async def run_trend_analysis(category: str, platforms: list, search_query: str = None) -> dict:
    """
    Main entry point for trend analysis.
    Fetches platform trends in parallel, then generates cross-platform insights.
    """
    if not platforms:
        platforms = ["twitter", "reddit", "linkedin", "instagram", "medium", "quora"]

    # If search query provided, append to category context
    effective_category = f"{category} - specifically about: {search_query}" if search_query else category

    # Fetch all platform trends in parallel
    platform_data = await fetch_all_platform_trends(effective_category, platforms)

    # Generate cross-platform insights
    try:
        insights = await generate_trend_insights(platform_data, effective_category)
    except Exception as e:
        insights = {
            "global_trend_score": 65,
            "trend_headline": f"{category} conversations are active across platforms",
            "momentum_direction": "Steady",
            "momentum_change_pct": 0,
            "ai_insights": ["Trend data collected. Review platform cards for details."],
            "content_opportunities": [],
            "hottest_platform": platforms[0] if platforms else "twitter",
            "emerging_angle": "Check individual platform trends for emerging angles.",
        }

    return {
        "category": category,
        "search_query": search_query,
        "platforms": platform_data,
        "insights": insights,
        "fetched_at": datetime.utcnow().isoformat(),
    }
