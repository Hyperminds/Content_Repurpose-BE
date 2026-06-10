"""
Campaign Performance Analytics Service.
Generates real AI-driven quality scores based on actual content analysis.
NO fake social metrics. All scores derived from content quality signals.
"""

import re
import json
from datetime import datetime, timezone
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
MODEL = "openai/gpt-4o-mini"

campaign_analytics_collection = db["campaign_analytics"]
campaign_content_collection   = db["campaign_content"]
campaign_days_collection      = db["campaign_days"]

# ── Platform-specific scoring weights ────────────────────────────────────────
PLATFORM_CHAR_LIMITS = {
    "linkedin": 3000, "twitter": 220, "instagram": 2200,
    "reddit": 40000, "medium": 99999, "meta": 63206, "quora": 99999,
}

PLATFORM_OPTIMAL_LENGTHS = {
    "linkedin": (150, 700), "twitter": (100, 220), "instagram": (100, 400),
    "reddit": (200, 2000), "medium": (500, 5000), "meta": (100, 500), "quora": (200, 1500),
}

VIRAL_PATTERNS = [
    r"\b(secret|hidden|nobody tells you|most people don't|truth about)\b",
    r"\b(game.changer|changed my life|transformed|breakthrough)\b",
    r"\b(stop doing|you're doing.*wrong|mistake|avoid this)\b",
    r"\b(unpopular opinion|hot take|controversial)\b",
    r"\b(in \d+ (days|weeks|months)|from \d+ to \d+)\b",
]

WEAK_CTA = [r"\b(click here|learn more|check it out|visit our|follow us)\b"]
STRONG_CTA = [
    r"\b(what do you think|share your|drop a comment|tell me|agree\?|thoughts\?)\b",
    r"\b(save this|bookmark|repost|share if|tag someone)\b",
]

EMOTIONAL_TRIGGERS = {
    "curiosity": [r"\b(secret|hidden|discover|reveal|truth|why|how)\b"],
    "fear": [r"\b(mistake|wrong|avoid|danger|risk|losing|fail)\b"],
    "aspiration": [r"\b(success|achieve|dream|goal|potential|transform)\b"],
    "urgency": [r"\b(now|today|don't wait|limited|before it's too late)\b"],
}


# ── Score Calculators (pure content analysis, no fake data) ──────────────────

def score_hook(content: str, platform: str) -> dict:
    """Score the opening hook quality (0-100)."""
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    hook = lines[0] if lines else ""
    score = 50
    reasons = []

    if len(hook) < 20:
        score -= 20
        reasons.append("Hook is too short")
    elif len(hook) < 80:
        score += 15
        reasons.append("Concise hook length")
    elif len(hook) > 150:
        score -= 10
        reasons.append("Hook is too long — trim for impact")

    for pattern in VIRAL_PATTERNS:
        if re.search(pattern, hook, re.IGNORECASE):
            score += 20
            reasons.append("Contains viral trigger phrase")
            break

    if hook.endswith("?"):
        score += 10
        reasons.append("Question hook drives curiosity")

    if re.search(r'\d+', hook):
        score += 8
        reasons.append("Number in hook increases credibility")

    if platform == "twitter" and len(hook) <= 100:
        score += 10
    elif platform == "linkedin" and len(hook) > 30:
        score += 5

    return {"score": min(100, max(0, score)), "reasons": reasons[:3]}


def score_cta(content: str) -> dict:
    """Score CTA effectiveness (0-100)."""
    text_lower = content.lower()
    score = 30
    label = "No CTA"
    reasons = []

    for pattern in STRONG_CTA:
        if re.search(pattern, text_lower):
            score = 85
            label = "Strong CTA"
            reasons.append("Engagement-driving CTA detected")
            break

    if score < 85:
        for pattern in WEAK_CTA:
            if re.search(pattern, text_lower):
                score = 40
                label = "Weak CTA"
                reasons.append("Generic CTA — replace with engagement question")
                break

    if score == 30:
        if "?" in content[-150:]:
            score = 65
            label = "Implicit CTA"
            reasons.append("Closing question acts as soft CTA")
        else:
            reasons.append("No clear CTA — add a question or call to action")

    return {"score": score, "label": label, "reasons": reasons}


def score_readability(content: str) -> dict:
    """Score readability (0-100)."""
    score = 60
    reasons = []
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    words = content.split()
    word_count = len(words)

    avg_line_len = sum(len(l) for l in lines) / max(len(lines), 1)
    if avg_line_len < 80:
        score += 15
        reasons.append("Short paragraphs improve mobile readability")
    elif avg_line_len > 200:
        score -= 15
        reasons.append("Long paragraphs hurt readability — break them up")

    if 50 <= word_count <= 250:
        score += 15
        reasons.append("Optimal word count for engagement")
    elif word_count > 400:
        score -= 10
        reasons.append("Content may be too long — consider trimming")
    elif word_count < 30:
        score -= 10
        reasons.append("Content too short — add more value")

    sentences = re.split(r'[.!?]+', content)
    lengths = [len(s.split()) for s in sentences if s.strip()]
    if lengths and max(lengths) - min(lengths) > 5:
        score += 10
        reasons.append("Good sentence variety improves flow")

    return {"score": min(100, max(0, score)), "word_count": word_count, "reasons": reasons[:3]}


def score_platform_optimization(content: str, platform: str) -> dict:
    """Score platform-specific optimization (0-100)."""
    score = 70
    reasons = []
    char_count = len(content)
    hashtag_count = len(re.findall(r'#\w+', content))

    limit = PLATFORM_CHAR_LIMITS.get(platform, 5000)
    opt_min, opt_max = PLATFORM_OPTIMAL_LENGTHS.get(platform, (100, 1000))

    if char_count > limit:
        score = 10
        reasons.append(f"Content exceeds {platform} character limit ({limit})")
    elif opt_min <= char_count <= opt_max:
        score += 15
        reasons.append(f"Optimal length for {platform}")
    elif char_count < opt_min:
        score -= 15
        reasons.append(f"Too short for {platform} — add more depth")
    elif char_count > opt_max:
        score -= 10
        reasons.append(f"Slightly long for {platform} — consider trimming")

    # Platform-specific hashtag rules
    if platform in ("reddit", "quora") and hashtag_count > 0:
        score -= 20
        reasons.append(f"{platform} doesn't use hashtags — remove them")
    elif platform == "twitter" and hashtag_count > 3:
        score -= 15
        reasons.append("Too many hashtags for Twitter — use 1-2 max")
    elif platform == "instagram" and 10 <= hashtag_count <= 25:
        score += 10
        reasons.append("Good hashtag count for Instagram discoverability")
    elif platform == "linkedin" and 3 <= hashtag_count <= 8:
        score += 10
        reasons.append("Optimal hashtag count for LinkedIn")

    return {"score": min(100, max(0, score)), "char_count": char_count, "hashtag_count": hashtag_count, "reasons": reasons[:3]}


def score_audience_alignment(content: str, target_audience: str, tone: str) -> dict:
    """Score audience alignment (0-100) based on tone and language signals."""
    score = 65
    reasons = []
    text_lower = content.lower()

    # Tone alignment
    tone_signals = {
        "professional": [r"\b(strategy|insights|results|growth|leadership|expertise)\b"],
        "casual": [r"\b(hey|awesome|cool|love|amazing|wow)\b"],
        "educational": [r"\b(learn|understand|discover|tip|guide|how to|step)\b"],
        "inspirational": [r"\b(believe|achieve|dream|possible|journey|success)\b"],
    }

    tone_lower = tone.lower() if tone else ""
    for t, patterns in tone_signals.items():
        if t in tone_lower:
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    score += 15
                    reasons.append(f"Content tone aligns with {tone} style")
                    break

    # Audience-specific signals
    if target_audience:
        audience_lower = target_audience.lower()
        if "developer" in audience_lower or "tech" in audience_lower:
            if re.search(r'\b(code|api|build|deploy|stack|framework)\b', text_lower):
                score += 10
                reasons.append("Technical language resonates with developer audience")
        if "founder" in audience_lower or "startup" in audience_lower:
            if re.search(r'\b(growth|scale|product|market|revenue|traction)\b', text_lower):
                score += 10
                reasons.append("Startup language aligns with founder audience")
        if "marketer" in audience_lower:
            if re.search(r'\b(campaign|conversion|funnel|engagement|brand|content)\b', text_lower):
                score += 10
                reasons.append("Marketing language resonates with marketer audience")

    if not reasons:
        reasons.append("Ensure content language matches your target audience")

    return {"score": min(100, max(0, score)), "reasons": reasons[:3]}


def score_emotional_trigger(content: str) -> dict:
    """Score emotional trigger strength (0-100)."""
    text_lower = content.lower()
    best_emotion = None
    score = 30

    for emotion, patterns in EMOTIONAL_TRIGGERS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                score = 75
                best_emotion = emotion
                break
        if best_emotion:
            break

    reasons = []
    if best_emotion:
        reasons.append(f"Triggers {best_emotion} — strong engagement signal")
    else:
        reasons.append("No clear emotional trigger — add curiosity or aspiration")

    return {"score": score, "emotion": best_emotion, "reasons": reasons}


# ── Campaign-level Analytics ──────────────────────────────────────────────────

async def analyze_campaign_content(campaign_id: str, campaign: dict) -> dict:
    """
    Analyze all approved/generated content for a campaign.
    Returns aggregate scores and AI recommendations.
    """
    # Get all content for this campaign
    cursor = campaign_content_collection.find({"campaign_id": campaign_id})
    content_docs = await cursor.to_list(length=200)

    if not content_docs:
        return {"error": "No content generated yet", "scores": {}, "recommendations": []}

    # Get corresponding days for platform info
    day_ids = [doc.get("day_id") for doc in content_docs]
    from bson import ObjectId
    days_cursor = campaign_days_collection.find({"_id": {"$in": [ObjectId(d) for d in day_ids if d]}})
    days_list = await days_cursor.to_list(length=200)
    days_map = {str(d["_id"]): d for d in days_list}

    # Score each piece of content
    all_scores = {
        "hook": [], "cta": [], "readability": [],
        "platform_optimization": [], "audience_alignment": [], "emotional_trigger": [],
    }
    platform_scores = {}
    pillar_scores = {}
    content_type_scores = {}

    for doc in content_docs:
        content = doc.get("content", "")
        if not content:
            continue

        day = days_map.get(doc.get("day_id"), {})
        platform = day.get("platform", "linkedin")
        pillar = day.get("content_pillar", "")
        content_type = day.get("content_type", "")

        hook_r    = score_hook(content, platform)
        cta_r     = score_cta(content)
        read_r    = score_readability(content)
        plat_r    = score_platform_optimization(content, platform)
        aud_r     = score_audience_alignment(content, campaign.get("target_audience", ""), campaign.get("tone", ""))
        emo_r     = score_emotional_trigger(content)

        all_scores["hook"].append(hook_r["score"])
        all_scores["cta"].append(cta_r["score"])
        all_scores["readability"].append(read_r["score"])
        all_scores["platform_optimization"].append(plat_r["score"])
        all_scores["audience_alignment"].append(aud_r["score"])
        all_scores["emotional_trigger"].append(emo_r["score"])

        # Track by platform
        if platform not in platform_scores:
            platform_scores[platform] = []
        overall = int((hook_r["score"] + cta_r["score"] + read_r["score"] + plat_r["score"]) / 4)
        platform_scores[platform].append(overall)

        # Track by pillar
        if pillar:
            if pillar not in pillar_scores:
                pillar_scores[pillar] = []
            pillar_scores[pillar].append(overall)

        # Track by content type
        if content_type:
            if content_type not in content_type_scores:
                content_type_scores[content_type] = []
            content_type_scores[content_type].append(overall)

    # Calculate averages
    def avg(lst): return int(sum(lst) / len(lst)) if lst else 0

    scores = {
        "hook_quality": avg(all_scores["hook"]),
        "cta_effectiveness": avg(all_scores["cta"]),
        "readability": avg(all_scores["readability"]),
        "platform_optimization": avg(all_scores["platform_optimization"]),
        "audience_alignment": avg(all_scores["audience_alignment"]),
        "emotional_trigger": avg(all_scores["emotional_trigger"]),
        "overall": avg([
            avg(all_scores["hook"]), avg(all_scores["cta"]),
            avg(all_scores["readability"]), avg(all_scores["platform_optimization"]),
            avg(all_scores["audience_alignment"]), avg(all_scores["emotional_trigger"]),
        ]),
        "content_analyzed": len(content_docs),
    }

    # Platform breakdown
    platform_breakdown = {p: avg(s) for p, s in platform_scores.items()}
    pillar_breakdown   = {p: avg(s) for p, s in pillar_scores.items()}
    content_type_breakdown = {ct: avg(s) for ct, s in content_type_scores.items()}

    # Generate AI recommendations
    recommendations = await _generate_recommendations(scores, platform_breakdown, pillar_breakdown, content_type_breakdown, campaign)

    result = {
        "scores": scores,
        "platform_breakdown": platform_breakdown,
        "pillar_breakdown": pillar_breakdown,
        "content_type_breakdown": content_type_breakdown,
        "recommendations": recommendations,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Cache in DB
    await campaign_analytics_collection.update_one(
        {"campaign_id": campaign_id},
        {"$set": {"campaign_id": campaign_id, "user_id": campaign.get("user_id"), **result}},
        upsert=True,
    )

    return result


async def _generate_recommendations(scores, platform_breakdown, pillar_breakdown, content_type_breakdown, campaign) -> list:
    """Generate AI-powered recommendations based on score patterns."""
    prompt = f"""You are a content strategy expert. Based on these campaign content quality scores, generate 4-6 specific, actionable recommendations.

SCORES (0-100):
- Hook Quality: {scores['hook_quality']}
- CTA Effectiveness: {scores['cta_effectiveness']}
- Readability: {scores['readability']}
- Platform Optimization: {scores['platform_optimization']}
- Audience Alignment: {scores['audience_alignment']}
- Emotional Trigger: {scores['emotional_trigger']}

PLATFORM SCORES: {platform_breakdown}
PILLAR SCORES: {pillar_breakdown}
CONTENT TYPE SCORES: {content_type_breakdown}

CAMPAIGN: {campaign.get('campaign_name')} — {campaign.get('campaign_type')}
AUDIENCE: {campaign.get('target_audience')}

Generate 4-6 specific recommendations. Each should:
- Be actionable and specific
- Reference actual score data
- Suggest concrete improvements

Return as JSON array of objects:
[
  {{"priority": "high|medium|low", "category": "Hook|CTA|Readability|Platform|Audience|Content Mix", "recommendation": "Specific actionable recommendation", "impact": "Expected improvement"}}
]

Return ONLY valid JSON array."""

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=600,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"[Analytics] Recommendation generation failed: {e}")
        return _fallback_recommendations(scores)


def _fallback_recommendations(scores) -> list:
    """Fallback recommendations based on score thresholds."""
    recs = []
    if scores["hook_quality"] < 60:
        recs.append({"priority": "high", "category": "Hook", "recommendation": "Hooks are too weak — start with a question, bold claim, or surprising statistic", "impact": "Can increase engagement by 2-3x"})
    if scores["cta_effectiveness"] < 50:
        recs.append({"priority": "high", "category": "CTA", "recommendation": "Add stronger CTAs — replace generic phrases with engagement questions", "impact": "Drives more comments and shares"})
    if scores["readability"] < 60:
        recs.append({"priority": "medium", "category": "Readability", "recommendation": "Break long paragraphs into shorter ones for mobile readability", "impact": "Reduces drop-off rate"})
    if scores["platform_optimization"] < 65:
        recs.append({"priority": "medium", "category": "Platform", "recommendation": "Optimize content length and hashtag count per platform rules", "impact": "Improves algorithmic reach"})
    if scores["emotional_trigger"] < 50:
        recs.append({"priority": "medium", "category": "Audience", "recommendation": "Add emotional triggers — curiosity, aspiration, or urgency language", "impact": "Increases content resonance"})
    return recs


async def get_cached_analytics(campaign_id: str) -> dict:
    """Get cached analytics for a campaign."""
    doc = await campaign_analytics_collection.find_one({"campaign_id": campaign_id})
    if not doc:
        return None
    doc.pop("_id", None)
    return doc
