"""
Social Presence Analyzer Service
In development mode (APP_ENV=development), returns mock data without any AI calls.
"""

import os
import json
from pathlib import Path
from openai import AsyncOpenAI
from dotenv import load_dotenv
from app.config import USE_MOCK
from app.mock_data.social_presence import get_mock_full_analysis

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = AsyncOpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

MODEL = "openai/gpt-4o-mini"

PLATFORM_ANALYSIS_PROMPTS = {
    "linkedin": {
        "focus": "professional authority, thought leadership, career branding, B2B networking",
        "ideal_frequency": "3-5 posts per week",
        "key_metrics": ["profile completeness", "headline clarity", "summary depth", "posting consistency", "CTA quality"],
    },
    "twitter": {
        "focus": "real-time engagement, trending topics, concise messaging, community building",
        "ideal_frequency": "1-3 tweets per day",
        "key_metrics": ["bio clarity", "pinned tweet quality", "posting frequency", "hook strength", "hashtag strategy"],
    },
    "instagram": {
        "focus": "visual storytelling, brand aesthetics, community engagement, carousel content",
        "ideal_frequency": "4-7 posts per week",
        "key_metrics": ["bio optimization", "visual consistency", "caption quality", "hashtag usage", "CTA effectiveness"],
    },
    "reddit": {
        "focus": "community value, authentic discussion, niche expertise, upvote-worthy content",
        "ideal_frequency": "2-4 posts per week",
        "key_metrics": ["subreddit targeting", "title quality", "content depth", "community engagement", "authenticity"],
    },
    "medium": {
        "focus": "long-form thought leadership, SEO discoverability, reader retention, publication strategy",
        "ideal_frequency": "2-4 articles per month",
        "key_metrics": ["headline quality", "bio completeness", "publication targeting", "content depth", "SEO optimization"],
    },
    "quora": {
        "focus": "expertise demonstration, SEO-driven answers, credibility building, knowledge sharing",
        "ideal_frequency": "3-5 answers per week",
        "key_metrics": ["profile bio", "credentials display", "answer quality", "topic expertise", "upvote strategy"],
    },
}


async def analyze_platform_profile(platform: str, profile_data: dict) -> dict:
    """
    Analyze a single platform profile and return structured scoring + recommendations.
    Works with just a username — AI infers profile quality from the handle and any
    additional context provided.
    """
    platform_config = PLATFORM_ANALYSIS_PROMPTS.get(platform, {})
    focus = platform_config.get("focus", "general social media presence")
    ideal_frequency = platform_config.get("ideal_frequency", "regular posting")
    key_metrics = platform_config.get("key_metrics", [])

    username = profile_data.get("username", "").strip()
    has_extra_data = any(
        profile_data.get(k)
        for k in ["bio", "posts_per_week", "followers", "primary_topics", "content_types"]
        if k != "username"
    )

    profile_info = json.dumps(profile_data, indent=2)

    # Build context note for the AI
    if not has_extra_data and username:
        context_note = f"""The user has only provided their username: "{username}".
Analyze based on what can be inferred from the username style, format, and typical {platform} profile patterns.
Score conservatively — without seeing the actual profile content, assume moderate completeness.
Generate realistic, platform-specific recommendations that would apply to most {platform} users at this stage."""
    else:
        context_note = "Analyze based on all provided profile data."

    profile_info = json.dumps(profile_data, indent=2)

    prompt = f"""You are an expert social media strategist analyzing a {platform.title()} profile.

Platform Focus: {focus}
Ideal Posting Frequency: {ideal_frequency}
Key Metrics to Evaluate: {', '.join(key_metrics)}

Context: {context_note}

Profile Data Provided:
{profile_info}

Analyze this profile and return a JSON response with EXACTLY this structure:
{{
  "platform": "{platform}",
  "score": <integer 0-100>,
  "grade": "<A/B/C/D/F>",
  "strengths": ["<strength 1>", "<strength 2>", "<strength 3>"],
  "weaknesses": ["<weakness 1>", "<weakness 2>", "<weakness 3>"],
  "growth_opportunities": ["<opportunity 1>", "<opportunity 2>"],
  "recommendations": [
    {{
      "priority": "high",
      "category": "<category like Bio, Posting Frequency, Branding, etc>",
      "title": "<short action title>",
      "description": "<specific, actionable recommendation in 1-2 sentences>",
      "impact": "<expected impact>"
    }},
    {{
      "priority": "medium",
      "category": "<category>",
      "title": "<short action title>",
      "description": "<specific, actionable recommendation>",
      "impact": "<expected impact>"
    }},
    {{
      "priority": "low",
      "category": "<category>",
      "title": "<short action title>",
      "description": "<specific, actionable recommendation>",
      "impact": "<expected impact>"
    }}
  ],
  "profile_completeness": <integer 0-100>,
  "posting_consistency": <integer 0-100>,
  "branding_quality": <integer 0-100>,
  "content_quality": <integer 0-100>,
  "cta_effectiveness": <integer 0-100>
}}

Rules:
- Be specific and platform-native in your recommendations
- Score honestly based on the data provided
- If profile data is minimal/missing, score lower and recommend completing the profile
- Recommendations must be practical and immediately actionable
- Return ONLY valid JSON, no markdown, no extra text
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "You are a social media strategy expert. Always respond with valid JSON only. No markdown, no explanation, just the JSON object."
            },
            {"role": "user", "content": prompt}
        ],
        temperature=0.4,
        max_tokens=800,
    )

    raw = response.choices[0].message.content.strip()
    # Strip markdown code fences if model adds them
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)


async def generate_overall_analysis(platform_results: list, connected_platforms: list) -> dict:
    """
    Generate an overall social presence score and cross-platform strategy.
    """
    platform_summary = []
    for r in platform_results:
        platform_summary.append({
            "platform": r.get("platform"),
            "score": r.get("score"),
            "top_weakness": r.get("weaknesses", [""])[0] if r.get("weaknesses") else "",
        })

    summary_json = json.dumps(platform_summary, indent=2)
    connected_str = ", ".join(connected_platforms)

    prompt = f"""You are a senior social media strategist reviewing a creator's multi-platform presence.

Connected Platforms: {connected_str}
Platform Scores:
{summary_json}

Generate a cross-platform strategy summary as JSON with EXACTLY this structure:
{{
  "overall_score": <integer 0-100, weighted average>,
  "overall_grade": "<A/B/C/D/F>",
  "presence_level": "<Beginner/Developing/Established/Authority/Influencer>",
  "summary": "<2-3 sentence honest assessment of their overall social presence>",
  "top_priority": "<single most important thing they should do right now>",
  "cross_platform_insights": [
    "<insight about how their platforms work together or against each other>",
    "<insight about content repurposing opportunities>",
    "<insight about audience overlap or gaps>"
  ],
  "improvement_roadmap": [
    {{
      "week": "Week 1-2",
      "focus": "<platform or theme>",
      "action": "<specific action to take>"
    }},
    {{
      "week": "Week 3-4",
      "focus": "<platform or theme>",
      "action": "<specific action to take>"
    }},
    {{
      "week": "Month 2",
      "focus": "<platform or theme>",
      "action": "<specific action to take>"
    }}
  ]
}}

Return ONLY valid JSON. No markdown, no extra text.
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "You are a social media strategy expert. Always respond with valid JSON only."
            },
            {"role": "user", "content": prompt}
        ],
        temperature=0.4,
        max_tokens=600,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)


async def run_social_presence_analysis(profiles_data: dict) -> dict:
    """
    Main entry point. In development mode returns mock data instantly.
    In production, runs full AI analysis in parallel.
    """
    import asyncio

    connected_platforms = list(profiles_data.keys())

    if not connected_platforms:
        return {"error": "No platform data provided", "platform_analyses": [], "overall": None}

    # ── DEVELOPMENT MODE ─────────────────────────────────────────────────────
    if USE_MOCK:
        return get_mock_full_analysis(profiles_data)

    # ── PRODUCTION MODE ──────────────────────────────────────────────────────

    # Run all platform analyses in parallel
    tasks = [
        analyze_platform_profile(platform, data)
        for platform, data in profiles_data.items()
    ]

    platform_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out exceptions, replace with error placeholders
    clean_results = []
    for i, result in enumerate(platform_results):
        platform = connected_platforms[i]
        if isinstance(result, Exception):
            clean_results.append({
                "platform": platform,
                "score": 0,
                "grade": "F",
                "error": str(result),
                "strengths": [],
                "weaknesses": ["Analysis failed — please try again"],
                "growth_opportunities": [],
                "recommendations": [],
                "profile_completeness": 0,
                "posting_consistency": 0,
                "branding_quality": 0,
                "content_quality": 0,
                "cta_effectiveness": 0,
            })
        else:
            clean_results.append(result)

    # Generate overall analysis
    try:
        overall = await generate_overall_analysis(clean_results, connected_platforms)
    except Exception as e:
        overall = {
            "overall_score": sum(r.get("score", 0) for r in clean_results) // max(len(clean_results), 1),
            "overall_grade": "C",
            "presence_level": "Developing",
            "summary": "Analysis complete. Review individual platform scores for details.",
            "top_priority": "Focus on completing your profile bios across all platforms.",
            "cross_platform_insights": [],
            "improvement_roadmap": [],
        }

    return {
        "platform_analyses": clean_results,
        "overall": overall,
        "analyzed_platforms": connected_platforms,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CONTENT INTELLIGENCE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

CONTENT_STYLE_LABELS = [
    "Educational", "Promotional", "Storytelling",
    "Authority Building", "Personal Branding", "Entertainment",
    "Thought Leadership", "Community Engagement",
]

PLATFORM_CONTENT_EXPECTATIONS = {
    "linkedin": "structured professional posts, thought leadership, career insights, B2B value",
    "twitter":  "concise punchy takes, threads, real-time commentary, strong hooks under 280 chars",
    "instagram":"visual-first captions, emotional hooks, lifestyle storytelling, strong CTAs",
    "reddit":   "authentic long-form discussion, community value, no self-promotion, conversational",
    "medium":   "long-form articles, deep analysis, strong headlines, SEO-friendly structure",
    "quora":    "expert answers, first-person authority, structured paragraphs, no hashtags",
}


async def analyze_single_content(content: str, platform: str) -> dict:
    """
    Deep AI analysis of a single piece of content for a given platform.
    Returns hook strength, readability, CTA quality, platform fit, style detection, and feedback.
    """
    platform_expectation = PLATFORM_CONTENT_EXPECTATIONS.get(
        platform, "general social media content"
    )
    styles_list = ", ".join(CONTENT_STYLE_LABELS)

    prompt = f"""You are an expert content strategist analyzing a {platform.title()} post.

Platform expectations: {platform_expectation}

Content to analyze:
\"\"\"
{content}
\"\"\"

Return a JSON object with EXACTLY this structure:
{{
  "hook_strength": <integer 0-100>,
  "readability": <integer 0-100>,
  "cta_quality": <integer 0-100>,
  "engagement_potential": <integer 0-100>,
  "content_clarity": <integer 0-100>,
  "platform_fit": <integer 0-100>,
  "overall_score": <integer 0-100, weighted average>,
  "content_style": "<one of: {styles_list}>",
  "platform_fit_verdict": "<Excellent Fit / Good Fit / Needs Adjustment / Poor Fit>",
  "platform_fit_reason": "<1 sentence explaining why this content does or doesn't fit {platform}>",
  "hook_analysis": "<1-2 sentences evaluating the opening hook>",
  "readability_analysis": "<1 sentence on paragraph length, sentence structure, flow>",
  "cta_analysis": "<1 sentence evaluating the call-to-action or lack thereof>",
  "ai_feedback": [
    "<specific, actionable improvement suggestion 1>",
    "<specific, actionable improvement suggestion 2>",
    "<specific, actionable improvement suggestion 3>"
  ],
  "strengths": ["<content strength 1>", "<content strength 2>"],
  "improvements": ["<improvement area 1>", "<improvement area 2>"]
}}

Rules:
- Score honestly. A weak hook should score below 40.
- ai_feedback must be specific and immediately actionable, not generic.
- Do NOT mention fake engagement numbers or metrics.
- Return ONLY valid JSON. No markdown, no extra text.
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a content strategy expert. Respond with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.35,
        max_tokens=700,
    )

    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


async def analyze_content_batch(content_items: list) -> dict:
    """
    Analyze multiple content pieces across platforms.
    content_items: [{"platform": "linkedin", "content": "...", "id": "optional"}, ...]
    Returns per-item analysis + aggregate insights.
    """
    import asyncio

    if not content_items:
        return {"items": [], "insights": None}

    tasks = [
        analyze_single_content(item["content"], item["platform"])
        for item in content_items
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    analyzed_items = []
    for i, result in enumerate(results):
        item = content_items[i]
        if isinstance(result, Exception):
            analyzed_items.append({
                "id": item.get("id", str(i)),
                "platform": item["platform"],
                "content_preview": item["content"][:80] + "..." if len(item["content"]) > 80 else item["content"],
                "error": str(result),
                "overall_score": 0,
            })
        else:
            analyzed_items.append({
                "id": item.get("id", str(i)),
                "platform": item["platform"],
                "content_preview": item["content"][:80] + "..." if len(item["content"]) > 80 else item["content"],
                **result,
            })

    # Generate aggregate insights
    valid = [r for r in analyzed_items if "error" not in r]
    insights = _compute_content_insights(valid) if valid else None

    return {"items": analyzed_items, "insights": insights}


def _compute_content_insights(analyzed_items: list) -> dict:
    """Compute aggregate insights from multiple analyzed content pieces."""
    if not analyzed_items:
        return {}

    # Style distribution
    style_counts = {}
    for item in analyzed_items:
        style = item.get("content_style", "Unknown")
        style_counts[style] = style_counts.get(style, 0) + 1

    # Platform scores
    platform_scores = {}
    for item in analyzed_items:
        p = item["platform"]
        if p not in platform_scores:
            platform_scores[p] = []
        platform_scores[p].append(item.get("overall_score", 0))

    platform_avg = {
        p: round(sum(scores) / len(scores))
        for p, scores in platform_scores.items()
    }

    # Metric averages
    metrics = ["hook_strength", "readability", "cta_quality", "engagement_potential",
               "content_clarity", "platform_fit"]
    metric_avgs = {}
    for m in metrics:
        vals = [item.get(m, 0) for item in analyzed_items if item.get(m) is not None]
        metric_avgs[m] = round(sum(vals) / len(vals)) if vals else 0

    # Best / worst
    best_platform = max(platform_avg, key=platform_avg.get) if platform_avg else None
    worst_platform = min(platform_avg, key=platform_avg.get) if platform_avg else None
    dominant_style = max(style_counts, key=style_counts.get) if style_counts else None
    weakest_metric = min(metric_avgs, key=metric_avgs.get) if metric_avgs else None

    # Weekly trend simulation (last 7 days buckets based on item order)
    chunk = max(1, len(analyzed_items) // 7)
    weekly_trend = []
    for i in range(0, len(analyzed_items), chunk):
        bucket = analyzed_items[i:i + chunk]
        avg = round(sum(b.get("overall_score", 0) for b in bucket) / len(bucket))
        weekly_trend.append(avg)
    weekly_trend = weekly_trend[:7]

    return {
        "total_analyzed": len(analyzed_items),
        "overall_avg_score": round(sum(i.get("overall_score", 0) for i in analyzed_items) / len(analyzed_items)),
        "metric_averages": metric_avgs,
        "style_distribution": style_counts,
        "platform_scores": platform_avg,
        "best_platform": best_platform,
        "worst_platform": worst_platform,
        "dominant_style": dominant_style,
        "weakest_metric": weakest_metric,
        "weekly_trend": weekly_trend,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AI GROWTH STRATEGIST ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _strip_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


async def run_competitor_analysis(user_profile: dict, competitors: list) -> dict:
    """
    Compare user profile against competitor/creator profiles.
    user_profile: { platform, username, bio, posts_per_week, followers, content_types, ... }
    competitors: [{ name, platform, username, bio, posts_per_week, followers, content_types, notes }, ...]
    """
    user_json = json.dumps(user_profile, indent=2)
    comp_json = json.dumps(competitors, indent=2)
    platform = user_profile.get("platform", "social media")

    prompt = f"""You are a senior social media strategist performing a competitive analysis on {platform.title()}.

USER PROFILE:
{user_json}

COMPETITOR PROFILES:
{comp_json}

Analyze the user's profile against the competitors and return EXACTLY this JSON:
{{
  "platform": "{platform}",
  "user_score": <integer 0-100>,
  "competitor_scores": [
    {{"name": "<competitor name>", "score": <integer 0-100>, "strongest_area": "<their biggest strength>", "gap_opportunity": "<what user can exploit>"}}
  ],
  "branding_comparison": {{
    "user_rating": "<Strong/Medium/Weak>",
    "user_notes": "<1 sentence on user branding>",
    "competitor_avg": "<Strong/Medium/Weak>",
    "verdict": "<1 sentence on how user compares>"
  }},
  "posting_frequency_comparison": {{
    "user_frequency": "<user posts per week>",
    "competitor_avg_frequency": "<average competitor posts per week>",
    "verdict": "<1 sentence assessment>"
  }},
  "content_strategy_gaps": [
    "<gap 1 — what competitors do that user doesn't>",
    "<gap 2>",
    "<gap 3>"
  ],
  "platform_strengths": [
    "<area where user outperforms competitors>",
    "<area 2>"
  ],
  "competitive_advantages": [
    "<unique advantage user has or could develop>",
    "<advantage 2>"
  ],
  "action_plan": [
    {{"priority": "high", "action": "<specific action to close competitive gap>"}},
    {{"priority": "medium", "action": "<specific action>"}},
    {{"priority": "low", "action": "<specific action>"}}
  ],
  "market_position": "<Lagging/Competitive/Leading/Dominant>",
  "summary": "<2-3 sentence honest competitive assessment>"
}}

Rules:
- Be specific and honest. Do not inflate user scores.
- Gaps and advantages must be actionable and platform-specific.
- Return ONLY valid JSON. No markdown, no extra text.
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a competitive analysis expert. Respond with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=900,
    )
    return json.loads(_strip_json(response.choices[0].message.content))


async def run_growth_forecast(profile_data: dict, platforms: list) -> dict:
    """
    Predict growth opportunities, strongest platform potential, weak areas, audience alignment.
    profile_data: overall profile context
    platforms: list of platform names the user is on
    """
    profile_json = json.dumps(profile_data, indent=2)
    platforms_str = ", ".join(platforms)

    prompt = f"""You are an AI growth strategist forecasting social media growth potential.

User's Active Platforms: {platforms_str}
Profile Context:
{profile_json}

Generate a growth forecast and return EXACTLY this JSON:
{{
  "strongest_platform": "<platform with highest growth potential>",
  "strongest_platform_reason": "<why this platform has the most potential>",
  "weakest_platform": "<platform dragging down overall presence>",
  "weakest_platform_reason": "<why this platform is underperforming>",
  "growth_opportunities": [
    {{"platform": "<platform>", "opportunity": "<specific growth opportunity>", "potential": "<High/Medium/Low>"}},
    {{"platform": "<platform>", "opportunity": "<opportunity>", "potential": "<High/Medium/Low>"}},
    {{"platform": "<platform>", "opportunity": "<opportunity>", "potential": "<High/Medium/Low>"}}
  ],
  "weak_content_areas": [
    "<content area that needs improvement>",
    "<weak area 2>",
    "<weak area 3>"
  ],
  "audience_alignment": {{
    "score": <integer 0-100>,
    "verdict": "<Well Aligned/Partially Aligned/Misaligned>",
    "notes": "<1-2 sentences on how well content matches target audience>"
  }},
  "platform_opportunity_scores": {{
    "<platform1>": <integer 0-100>,
    "<platform2>": <integer 0-100>
  }},
  "consistency_score": <integer 0-100>,
  "growth_trajectory": "<Declining/Stagnant/Slow Growth/Moderate Growth/High Growth>",
  "forecast_6_months": "<realistic 1-2 sentence prediction of where they'll be in 6 months if they follow recommendations>",
  "top_3_actions": [
    "<most impactful action to take immediately>",
    "<second action>",
    "<third action>"
  ],
  "weekly_consistency_data": [<7 integers 0-100 representing estimated weekly consistency trend>]
}}

Rules:
- Be realistic. Do not promise unrealistic growth.
- Opportunity scores must reflect actual platform potential for this user.
- Return ONLY valid JSON. No markdown, no extra text.
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a growth strategy expert. Respond with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=800,
    )
    return json.loads(_strip_json(response.choices[0].message.content))


async def run_brand_positioning(profile_data: dict) -> dict:
    """
    Analyze niche clarity, authority level, personal branding consistency, audience targeting.
    Generate AI branding suggestions.
    """
    profile_json = json.dumps(profile_data, indent=2)

    prompt = f"""You are a personal branding expert analyzing a creator's brand positioning.

Profile Data:
{profile_json}

Analyze their brand and return EXACTLY this JSON:
{{
  "niche_clarity": {{
    "score": <integer 0-100>,
    "verdict": "<Crystal Clear/Somewhat Clear/Unclear/Scattered>",
    "notes": "<1-2 sentences on how clear their niche is>"
  }},
  "authority_level": {{
    "score": <integer 0-100>,
    "level": "<Beginner/Emerging/Established/Authority/Thought Leader>",
    "notes": "<1-2 sentences on their authority positioning>"
  }},
  "branding_consistency": {{
    "score": <integer 0-100>,
    "verdict": "<Highly Consistent/Mostly Consistent/Inconsistent/Fragmented>",
    "notes": "<1 sentence on cross-platform branding consistency>"
  }},
  "audience_targeting": {{
    "score": <integer 0-100>,
    "clarity": "<Well Defined/Partially Defined/Undefined>",
    "notes": "<1-2 sentences on how well they target their audience>"
  }},
  "brand_archetype": "<The Expert/The Creator/The Storyteller/The Challenger/The Guide/The Connector>",
  "brand_voice": "<Professional/Conversational/Inspirational/Educational/Entertaining/Authoritative>",
  "positioning_statement": "<AI-generated 1-sentence brand positioning statement for this creator>",
  "branding_suggestions": [
    {{"area": "<Bio/Headline/Visual Identity/Content Pillars/Tone/Niche>", "suggestion": "<specific actionable branding improvement>"}},
    {{"area": "<area>", "suggestion": "<suggestion>"}},
    {{"area": "<area>", "suggestion": "<suggestion>"}},
    {{"area": "<area>", "suggestion": "<suggestion>"}}
  ],
  "content_pillars": [
    "<recommended content pillar 1 for their niche>",
    "<pillar 2>",
    "<pillar 3>"
  ],
  "differentiation_opportunities": [
    "<how they can stand out from others in their niche>",
    "<differentiation opportunity 2>"
  ],
  "overall_brand_score": <integer 0-100>
}}

Rules:
- Be specific to their actual niche and content.
- Branding suggestions must be immediately actionable.
- Return ONLY valid JSON. No markdown, no extra text.
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a personal branding expert. Respond with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.45,
        max_tokens=800,
    )
    return json.loads(_strip_json(response.choices[0].message.content))


async def run_content_strategy(profile_data: dict, platforms: list) -> dict:
    """
    Generate monthly content plan, platform-specific strategies, posting recommendations, content mix.
    """
    profile_json = json.dumps(profile_data, indent=2)
    platforms_str = ", ".join(platforms)

    prompt = f"""You are a content strategist building a monthly content plan.

Active Platforms: {platforms_str}
Creator Profile:
{profile_json}

Generate a complete content strategy and return EXACTLY this JSON:
{{
  "monthly_content_plan": [
    {{"week": "Week 1", "theme": "<weekly content theme>", "platforms": [{{"platform": "<platform>", "content_type": "<type>", "topic": "<specific topic>", "frequency": "<posts per week>"}}]}},
    {{"week": "Week 2", "theme": "<theme>", "platforms": [{{"platform": "<platform>", "content_type": "<type>", "topic": "<topic>", "frequency": "<frequency>"}}]}},
    {{"week": "Week 3", "theme": "<theme>", "platforms": [{{"platform": "<platform>", "content_type": "<type>", "topic": "<topic>", "frequency": "<frequency>"}}]}},
    {{"week": "Week 4", "theme": "<theme>", "platforms": [{{"platform": "<platform>", "content_type": "<type>", "topic": "<topic>", "frequency": "<frequency>"}}]}}
  ],
  "platform_strategies": [
    {{"platform": "<platform>", "primary_goal": "<main goal for this platform>", "content_types": ["<type1>", "<type2>"], "posting_frequency": "<recommended frequency>", "best_times": "<best posting times>", "key_tactics": ["<tactic 1>", "<tactic 2>"]}}
  ],
  "content_mix": {{
    "educational": <integer percentage>,
    "storytelling": <integer percentage>,
    "promotional": <integer percentage>,
    "engagement": <integer percentage>,
    "authority": <integer percentage>
  }},
  "posting_recommendations": [
    {{"platform": "<platform>", "recommendation": "<specific posting recommendation>", "reason": "<why>"}}
  ],
  "content_repurposing_map": [
    {{"source": "<platform>", "repurpose_to": "<platform>", "how": "<how to adapt the content>"}}
  ],
  "monthly_focus": "<overarching monthly content focus/theme>",
  "kpis_to_track": [
    "<qualitative KPI 1 to monitor>",
    "<KPI 2>",
    "<KPI 3>"
  ]
}}

Rules:
- Make the plan realistic and immediately executable.
- Content types must be platform-native.
- Return ONLY valid JSON. No markdown, no extra text.
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a content strategy expert. Respond with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
        max_tokens=1000,
    )
    return json.loads(_strip_json(response.choices[0].message.content))


async def run_bio_optimization(platform: str, current_bio: str, profile_context: dict) -> dict:
    """
    Regenerate bio, improve headline, optimize CTA, improve profile positioning.
    """
    context_json = json.dumps(profile_context, indent=2)

    prompt = f"""You are a profile optimization expert specializing in {platform.title()} profiles.

Current Bio:
\"\"\"{current_bio}\"\"\"

Profile Context:
{context_json}

Generate optimized profile copy and return EXACTLY this JSON:
{{
  "optimized_bio": "<rewritten bio optimized for {platform} — platform-native length and style>",
  "bio_improvements": [
    "<specific improvement made and why>",
    "<improvement 2>",
    "<improvement 3>"
  ],
  "headline_options": [
    "<headline option 1 — strong authority positioning>",
    "<headline option 2 — niche-specific>",
    "<headline option 3 — value-driven>"
  ],
  "cta_options": [
    "<CTA option 1 — direct and action-oriented>",
    "<CTA option 2 — curiosity-driven>",
    "<CTA option 3 — value-focused>"
  ],
  "positioning_improvements": [
    {{"area": "<Bio Structure/Niche Clarity/Authority Signals/CTA/Keywords>", "before": "<what was weak>", "after": "<what was improved>"}},
    {{"area": "<area>", "before": "<before>", "after": "<after>"}}
  ],
  "keyword_suggestions": ["<keyword 1>", "<keyword 2>", "<keyword 3>"],
  "bio_score_before": <integer 0-100>,
  "bio_score_after": <integer 0-100>,
  "platform_specific_tips": [
    "<platform-native tip 1 for {platform}>",
    "<tip 2>",
    "<tip 3>"
  ]
}}

Rules:
- The optimized bio must feel natural and human, not AI-generated.
- Respect {platform} character limits and conventions.
- Return ONLY valid JSON. No markdown, no extra text.
"""

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a profile copywriting expert. Respond with valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.55,
        max_tokens=800,
    )
    return json.loads(_strip_json(response.choices[0].message.content))
