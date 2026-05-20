"""
AI Content Scoring Engine - analyzes generated content for quality, virality,
platform compatibility, and provides actionable improvement suggestions.
All scores are based on real content analysis, not fabricated metrics.
"""

import re
from datetime import datetime, timezone
from bson import ObjectId
from app.database import db

post_history_collection = db["post_history"]

# Best posting times by platform (heuristic-based, industry research)
BEST_POSTING_TIMES = {
    "linkedin": {"time": "9:00 AM", "days": "Tue–Thu", "reason": "Professional audience active during work hours"},
    "twitter": {"time": "7:30 PM", "days": "Mon–Wed", "reason": "Peak engagement during evening scroll"},
    "instagram": {"time": "11:00 AM", "days": "Mon, Wed, Fri", "reason": "Lunch-hour browsing peak"},
    "reddit": {"time": "11:00 PM", "days": "Mon–Thu", "reason": "Night-owl community activity"},
    "medium": {"time": "10:00 AM", "days": "Tue, Thu", "reason": "Morning reading habit"},
    "meta": {"time": "1:00 PM", "days": "Wed–Fri", "reason": "Post-lunch social browsing"},
    "quora": {"time": "3:00 PM", "days": "Mon–Fri", "reason": "Afternoon knowledge-seeking peak"},
}

# Viral phrases and patterns
VIRAL_PATTERNS = [
    r"\b(secret|hidden|nobody tells you|most people don't|what they don't|truth about)\b",
    r"\b(game.changer|game changer|changed my life|transformed|breakthrough)\b",
    r"\b(stop doing|you're doing.*wrong|mistake|avoid this)\b",
    r"\b(unpopular opinion|hot take|controversial|fight me)\b",
    r"\b(thread|🧵|here's why|let me explain)\b",
    r"\b(in \d+ (days|weeks|months)|from \d+ to \d+)\b",
]

WEAK_CTA_PATTERNS = [
    r"\b(click here|learn more|check it out|visit our|follow us)\b",
]

STRONG_CTA_PATTERNS = [
    r"\b(what do you think|share your|drop a comment|tell me|have you ever|agree\?|thoughts\?)\b",
    r"\b(save this|bookmark|repost|share if|tag someone)\b",
]

EMOTIONAL_TRIGGERS = {
    "curiosity": [r"\b(secret|hidden|discover|reveal|truth|why|how)\b"],
    "fear": [r"\b(mistake|wrong|avoid|danger|risk|losing|fail)\b"],
    "aspiration": [r"\b(success|achieve|dream|goal|potential|transform)\b"],
    "belonging": [r"\b(we|us|together|community|join|fellow)\b"],
    "urgency": [r"\b(now|today|don't wait|limited|before it's too late)\b"],
}


def analyze_content(content: str, platform: str) -> dict:
    """
    Full AI content analysis. Returns scores and insights.
    All analysis is based on actual content — no fabrication.
    """
    if not content or not content.strip():
        return _empty_analysis()

    text = content.strip()
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    words = text.split()
    word_count = len(words)
    char_count = len(text)

    # ---- HOOK STRENGTH (0-100) ----
    hook = lines[0] if lines else ""
    hook_score = _score_hook(hook, platform)

    # ---- READABILITY (0-100) ----
    readability = _score_readability(text, word_count, lines)

    # ---- CTA EFFECTIVENESS (0-100) ----
    cta_score, cta_label = _score_cta(text)

    # ---- HASHTAG QUALITY (0-100) ----
    hashtag_score, hashtag_count = _score_hashtags(text, platform)

    # ---- EMOTIONAL TRIGGER (0-100) ----
    emotional_score, detected_emotion = _score_emotional(text)

    # ---- PLATFORM COMPATIBILITY (0-100) ----
    platform_score = _score_platform_compat(text, platform, char_count, hashtag_count)

    # ---- VIRAL POTENTIAL ----
    viral_phrases = _detect_viral_phrases(text)
    viral_score = _calculate_viral_score(hook_score, emotional_score, cta_score, viral_phrases)
    viral_label = "High" if viral_score >= 70 else "Medium" if viral_score >= 40 else "Low"

    # ---- OVERALL CONTENT SCORE ----
    content_score = int(
        hook_score * 0.25 +
        readability * 0.20 +
        cta_score * 0.15 +
        hashtag_score * 0.10 +
        emotional_score * 0.15 +
        platform_score * 0.15
    )

    # ---- SENTENCE ANALYSIS ----
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]
    strongest = _find_strongest_sentence(sentences, viral_phrases)
    weakest = _find_weakest_sentence(sentences)

    # ---- TONE DETECTION ----
    tone = _detect_tone(text)

    # ---- IMPROVEMENT SUGGESTIONS ----
    suggestions = _generate_suggestions(
        hook_score, readability, cta_score, hashtag_score,
        emotional_score, platform_score, platform, word_count, char_count
    )

    # ---- PLATFORM-SPECIFIC INSIGHTS ----
    platform_insights = _platform_specific_insights(text, platform, hook_score, readability)

    return {
        "content_score": content_score,
        "hook_strength": hook_score,
        "readability": readability,
        "cta_effectiveness": cta_score,
        "cta_label": cta_label,
        "hashtag_quality": hashtag_score,
        "hashtag_count": hashtag_count,
        "emotional_trigger": emotional_score,
        "detected_emotion": detected_emotion,
        "platform_compatibility": platform_score,
        "viral_potential": viral_label,
        "viral_score": viral_score,
        "viral_phrases": viral_phrases[:3],
        "strongest_sentence": strongest,
        "weakest_sentence": weakest,
        "tone": tone,
        "word_count": word_count,
        "char_count": char_count,
        "suggestions": suggestions,
        "platform_insights": platform_insights,
        "best_posting_time": BEST_POSTING_TIMES.get(platform, {}),
    }


def analyze_all_platforms(content_map: dict) -> dict:
    """Analyze content for all platforms at once."""
    results = {}
    for platform, content in content_map.items():
        if content:
            results[platform] = analyze_content(content, platform)
    return results


def get_overall_score(analyses: dict) -> dict:
    """Calculate aggregate scores across all platforms."""
    if not analyses:
        return {"avg_score": 0, "best_platform": None, "viral_count": 0}

    scores = [a["content_score"] for a in analyses.values() if a]
    avg = int(sum(scores) / len(scores)) if scores else 0
    best = max(analyses.items(), key=lambda x: x[1]["content_score"] if x[1] else 0)
    viral_high = sum(1 for a in analyses.values() if a and a.get("viral_potential") == "High")

    return {
        "avg_score": avg,
        "best_platform": best[0] if best else None,
        "viral_count": viral_high,
        "total_suggestions": sum(len(a.get("suggestions", [])) for a in analyses.values() if a),
    }


# ============ SCORING FUNCTIONS ============ #

def _score_hook(hook: str, platform: str) -> int:
    score = 50
    if not hook:
        return 20

    # Length check
    if len(hook) < 60:
        score += 20
    elif len(hook) > 150:
        score -= 15

    # Viral patterns in hook
    for pattern in VIRAL_PATTERNS:
        if re.search(pattern, hook, re.IGNORECASE):
            score += 15
            break

    # Question hook
    if hook.endswith("?"):
        score += 10

    # Number in hook
    if re.search(r'\d+', hook):
        score += 10

    # Platform-specific
    if platform == "twitter" and len(hook) <= 100:
        score += 10
    elif platform == "linkedin" and len(hook) > 30:
        score += 5

    return min(100, max(0, score))


def _score_readability(text: str, word_count: int, lines: list) -> int:
    score = 60

    # Short paragraphs are more readable
    avg_line_len = sum(len(l) for l in lines) / max(len(lines), 1)
    if avg_line_len < 100:
        score += 15
    elif avg_line_len > 200:
        score -= 10

    # Word count
    if 50 <= word_count <= 300:
        score += 15
    elif word_count > 500:
        score -= 10

    # Sentence variety (mix of short and long)
    sentences = re.split(r'[.!?]+', text)
    lengths = [len(s.split()) for s in sentences if s.strip()]
    if lengths:
        variance = max(lengths) - min(lengths)
        if variance > 5:
            score += 10

    return min(100, max(0, score))


def _score_cta(text: str) -> tuple:
    text_lower = text.lower()

    # Check for strong CTA
    for pattern in STRONG_CTA_PATTERNS:
        if re.search(pattern, text_lower):
            return 85, "Strong CTA"

    # Check for weak CTA
    for pattern in WEAK_CTA_PATTERNS:
        if re.search(pattern, text_lower):
            return 40, "Weak CTA"

    # No CTA
    if "?" in text[-100:]:
        return 70, "Implicit CTA"

    return 30, "No CTA"


def _score_hashtags(text: str, platform: str) -> tuple:
    hashtags = re.findall(r'#\w+', text)
    count = len(hashtags)

    optimal = {"linkedin": (3, 8), "instagram": (10, 25), "twitter": (1, 3),
               "reddit": (0, 0), "medium": (3, 5), "meta": (2, 5), "quora": (0, 0)}

    low, high = optimal.get(platform, (2, 8))

    if platform in ("reddit", "quora"):
        return (100 if count == 0 else 50), count

    if count == 0:
        return 20, count
    elif low <= count <= high:
        return 90, count
    elif count < low:
        return 60, count
    else:
        return 50, count


def _score_emotional(text: str) -> tuple:
    text_lower = text.lower()
    best_emotion = None
    best_score = 0

    for emotion, patterns in EMOTIONAL_TRIGGERS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                score = 75
                if best_score < score:
                    best_score = score
                    best_emotion = emotion

    if best_score == 0:
        return 30, "neutral"

    return min(100, best_score), best_emotion


def _score_platform_compat(text: str, platform: str, char_count: int, hashtag_count: int) -> int:
    score = 70

    limits = {"twitter": 280, "linkedin": 3000, "instagram": 2200,
              "reddit": 40000, "medium": 999999, "meta": 63206, "quora": 999999}

    limit = limits.get(platform, 5000)
    if char_count > limit:
        return 10  # Over limit
    elif char_count < limit * 0.1:
        score -= 20  # Too short

    # Platform-specific checks
    if platform == "reddit" and hashtag_count > 0:
        score -= 20
    if platform == "twitter" and char_count <= 240:
        score += 20
    if platform == "linkedin" and 200 <= char_count <= 1500:
        score += 15

    return min(100, max(0, score))


def _calculate_viral_score(hook: int, emotional: int, cta: int, viral_phrases: list) -> int:
    base = (hook * 0.4 + emotional * 0.3 + cta * 0.2)
    bonus = min(20, len(viral_phrases) * 7)
    return min(100, int(base + bonus))


def _detect_viral_phrases(text: str) -> list:
    found = []
    for pattern in VIRAL_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        found.extend(matches)
    return list(set(found))


def _find_strongest_sentence(sentences: list, viral_phrases: list) -> str:
    if not sentences:
        return ""
    # Score each sentence
    scored = []
    for s in sentences[:10]:
        score = 0
        if any(p.lower() in s.lower() for p in viral_phrases):
            score += 3
        if s.endswith("?"):
            score += 2
        if re.search(r'\d+', s):
            score += 1
        if len(s.split()) < 20:
            score += 1
        scored.append((score, s))
    scored.sort(reverse=True)
    return scored[0][1] if scored else sentences[0]


def _find_weakest_sentence(sentences: list) -> str:
    if not sentences:
        return ""
    # Find longest, most generic sentence
    scored = []
    for s in sentences:
        score = len(s.split())  # Longer = potentially weaker
        if re.search(r'\b(the|this|that|it is|there are)\b', s.lower()):
            score += 5
        scored.append((score, s))
    scored.sort(reverse=True)
    return scored[0][1] if scored else ""


def _detect_tone(text: str) -> str:
    text_lower = text.lower()
    if re.search(r'\b(excited|amazing|incredible|love|fantastic|awesome)\b', text_lower):
        return "enthusiastic"
    if re.search(r'\b(research|data|study|analysis|evidence|proven)\b', text_lower):
        return "analytical"
    if re.search(r'\b(i believe|in my experience|personally|i think|i feel)\b', text_lower):
        return "personal"
    if re.search(r'\b(you should|you must|you need|avoid|stop)\b', text_lower):
        return "instructional"
    if re.search(r'\b(story|once|when i|remember|back in)\b', text_lower):
        return "storytelling"
    return "professional"


def _generate_suggestions(hook, readability, cta, hashtags, emotional, platform_compat, platform, word_count, char_count) -> list:
    suggestions = []

    if hook < 60:
        suggestions.append({"priority": "high", "icon": "🔥", "text": "Strengthen your opening hook — first line needs more impact to stop the scroll."})
    if cta < 50:
        suggestions.append({"priority": "high", "icon": "⚠", "text": "Add a stronger CTA — ask a question or invite engagement at the end."})
    if readability < 60:
        suggestions.append({"priority": "medium", "icon": "📖", "text": "Improve readability — break long paragraphs into shorter, punchier lines."})
    if emotional < 40:
        suggestions.append({"priority": "medium", "icon": "💡", "text": "Add an emotional trigger — curiosity, aspiration, or urgency drives more engagement."})
    if platform == "twitter" and char_count > 220:
        suggestions.append({"priority": "high", "icon": "✂", "text": "Tweet is too long — trim to under 220 characters for optimal reach."})
    if platform in ("linkedin", "instagram") and hashtags < 50:
        suggestions.append({"priority": "low", "icon": "#", "text": "Add more relevant hashtags to improve discoverability."})
    if word_count > 400 and platform == "twitter":
        suggestions.append({"priority": "high", "icon": "✂", "text": "Content too long for Twitter — consider a thread format."})

    return suggestions[:5]  # Max 5 suggestions


def _platform_specific_insights(text: str, platform: str, hook_score: int, readability: int) -> list:
    insights = []
    text_lower = text.lower()

    if platform == "linkedin":
        if re.search(r'\b(i|my|we|our)\b', text_lower):
            insights.append({"label": "Personal Voice", "status": "positive", "text": "First-person tone builds authority on LinkedIn."})
        if re.search(r'\b(data|research|study|results|percent|%)\b', text_lower):
            insights.append({"label": "Data-Backed", "status": "positive", "text": "Statistics and data increase credibility."})
        if hook_score < 60:
            insights.append({"label": "Hook Weak", "status": "warning", "text": "LinkedIn posts need a strong first line to expand the preview."})

    elif platform == "twitter":
        if len(text) <= 220:
            insights.append({"label": "Tweet Length", "status": "positive", "text": "Perfect length — leaves room for image attachment."})
        if re.search(r'#\w+', text):
            hashtag_count = len(re.findall(r'#\w+', text))
            if hashtag_count <= 2:
                insights.append({"label": "Hashtags", "status": "positive", "text": "Optimal hashtag count for Twitter."})
            else:
                insights.append({"label": "Too Many Hashtags", "status": "warning", "text": "Twitter performs better with 1-2 hashtags max."})

    elif platform == "reddit":
        if re.search(r'#\w+', text):
            insights.append({"label": "Hashtags Detected", "status": "warning", "text": "Reddit doesn't use hashtags — remove them for authenticity."})
        if re.search(r'\b(buy|sale|discount|promo|offer)\b', text_lower):
            insights.append({"label": "Promotional Tone", "status": "warning", "text": "Reddit communities penalize promotional content."})
        else:
            insights.append({"label": "Community Tone", "status": "positive", "text": "Content reads as authentic — good fit for Reddit."})

    elif platform == "medium":
        if readability >= 70:
            insights.append({"label": "Readability", "status": "positive", "text": "Well-structured for long-form reading."})
        if re.search(r'\b(story|experience|journey|learned)\b', text_lower):
            insights.append({"label": "Storytelling", "status": "positive", "text": "Narrative elements improve Medium engagement."})

    elif platform == "quora":
        if re.search(r'\b(in my experience|i found|i learned|personally)\b', text_lower):
            insights.append({"label": "Authority Voice", "status": "positive", "text": "Personal experience framing builds credibility on Quora."})
        if len(text.split()) < 100:
            insights.append({"label": "Answer Depth", "status": "warning", "text": "Quora answers perform better with more depth and detail."})

    return insights


def _empty_analysis() -> dict:
    return {
        "content_score": 0, "hook_strength": 0, "readability": 0,
        "cta_effectiveness": 0, "cta_label": "No CTA", "hashtag_quality": 0,
        "hashtag_count": 0, "emotional_trigger": 0, "detected_emotion": "neutral",
        "platform_compatibility": 0, "viral_potential": "Low", "viral_score": 0,
        "viral_phrases": [], "strongest_sentence": "", "weakest_sentence": "",
        "tone": "neutral", "word_count": 0, "char_count": 0,
        "suggestions": [], "platform_insights": [], "best_posting_time": {},
    }
