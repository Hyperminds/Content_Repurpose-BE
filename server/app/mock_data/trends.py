"""
Mock trend data for development mode.
Returns realistic trend intelligence without calling any external API.
"""

import random
from datetime import datetime

MOCK_TOPICS_BY_CATEGORY = {
    "AI": [
        {"topic": "AI Agents replacing SaaS tools", "hashtag": "#AIAgents", "momentum": "Viral", "momentum_pct": 142, "engagement_level": "High", "content_opportunity_score": 94, "why_trending": "Developers are building autonomous agents that replace entire software categories", "content_suggestion": "Write a LinkedIn thread comparing 5 AI agents vs their SaaS equivalents"},
        {"topic": "GPT-5 speculation and capabilities", "hashtag": "#GPT5", "momentum": "Rising", "momentum_pct": 87, "engagement_level": "High", "content_opportunity_score": 88, "why_trending": "OpenAI hints at next model release driving massive speculation", "content_suggestion": "Create a Twitter thread on what GPT-5 might mean for your industry"},
        {"topic": "Open source AI vs closed models", "hashtag": "#OpenSourceAI", "momentum": "Rising", "momentum_pct": 63, "engagement_level": "High", "content_opportunity_score": 82, "why_trending": "Llama and Mistral challenging OpenAI dominance", "content_suggestion": "LinkedIn carousel: Open source AI tools you can use today for free"},
        {"topic": "AI coding assistants productivity data", "hashtag": "#AIDevTools", "momentum": "Steady", "momentum_pct": 28, "engagement_level": "Medium", "content_opportunity_score": 76, "why_trending": "Real productivity studies showing 2-3x developer output increases", "content_suggestion": "Share your personal productivity data using AI coding tools"},
        {"topic": "Prompt engineering as a career", "hashtag": "#PromptEngineering", "momentum": "Declining", "momentum_pct": -12, "engagement_level": "Medium", "content_opportunity_score": 45, "why_trending": "Debate on whether prompt engineering is a real skill or temporary", "content_suggestion": "Contrarian take: Why prompt engineering will evolve, not disappear"},
        {"topic": "AI hallucination solutions in 2025", "hashtag": "#AIReliability", "momentum": "Rising", "momentum_pct": 54, "engagement_level": "High", "content_opportunity_score": 79, "why_trending": "New techniques for grounding AI responses gaining traction", "content_suggestion": "Educational post on RAG and how it solves hallucination problems"},
        {"topic": "Multimodal AI in enterprise workflows", "hashtag": "#MultimodalAI", "momentum": "Rising", "momentum_pct": 71, "engagement_level": "High", "content_opportunity_score": 85, "why_trending": "Companies integrating vision + text AI into business processes", "content_suggestion": "Case study carousel: How enterprises are using multimodal AI"},
        {"topic": "AI regulation and compliance updates", "hashtag": "#AIRegulation", "momentum": "Steady", "momentum_pct": 15, "engagement_level": "Medium", "content_opportunity_score": 68, "why_trending": "EU AI Act enforcement beginning, companies scrambling to comply", "content_suggestion": "LinkedIn article: What the EU AI Act means for your startup"},
    ],
    "Technology": [
        {"topic": "Rust replacing Python in AI infrastructure", "hashtag": "#RustLang", "momentum": "Rising", "momentum_pct": 48, "engagement_level": "High", "content_opportunity_score": 77, "why_trending": "Performance-critical AI systems moving to Rust for speed", "content_suggestion": "Thread: Why Rust is becoming the language of AI infrastructure"},
        {"topic": "WebAssembly in production apps", "hashtag": "#WASM", "momentum": "Rising", "momentum_pct": 39, "engagement_level": "Medium", "content_opportunity_score": 71, "why_trending": "WASM enabling near-native performance in browsers", "content_suggestion": "Tutorial post: Running AI models in the browser with WASM"},
        {"topic": "Edge computing and IoT convergence", "hashtag": "#EdgeAI", "momentum": "Steady", "momentum_pct": 22, "engagement_level": "Medium", "content_opportunity_score": 65, "why_trending": "Processing moving closer to data sources for latency reduction", "content_suggestion": "Explainer: Why edge AI matters for real-time applications"},
    ],
    "Startups": [
        {"topic": "Bootstrapped vs VC-funded debate 2025", "hashtag": "#Bootstrapped", "momentum": "Viral", "momentum_pct": 118, "engagement_level": "High", "content_opportunity_score": 91, "why_trending": "High-profile bootstrapped exits challenging VC narrative", "content_suggestion": "Share your honest take on bootstrapping vs raising in current market"},
        {"topic": "AI-native startups outperforming incumbents", "hashtag": "#AIStartups", "momentum": "Rising", "momentum_pct": 76, "engagement_level": "High", "content_opportunity_score": 87, "why_trending": "New AI-first companies disrupting established software categories", "content_suggestion": "LinkedIn post: 5 AI-native startups that are quietly winning"},
        {"topic": "Solo founder success stories", "hashtag": "#SoloFounder", "momentum": "Rising", "momentum_pct": 58, "engagement_level": "High", "content_opportunity_score": 83, "why_trending": "AI tools enabling single founders to build $1M+ ARR businesses", "content_suggestion": "Thread: How solo founders are using AI to compete with funded teams"},
    ],
    "Finance": [
        {"topic": "DeFi institutional adoption accelerating", "hashtag": "#DeFi", "momentum": "Rising", "momentum_pct": 44, "engagement_level": "Medium", "content_opportunity_score": 72, "why_trending": "Major banks launching DeFi products for institutional clients", "content_suggestion": "Explainer: What institutional DeFi adoption means for retail investors"},
        {"topic": "AI-powered financial planning tools", "hashtag": "#FinTech", "momentum": "Rising", "momentum_pct": 61, "engagement_level": "High", "content_opportunity_score": 80, "why_trending": "New AI tools democratizing wealth management", "content_suggestion": "Review carousel: Best AI financial planning tools in 2025"},
    ],
}

# Default fallback topics for any category
DEFAULT_TOPICS = MOCK_TOPICS_BY_CATEGORY["AI"]


def get_mock_platform_trends(platform: str, category: str) -> dict:
    """Return mock trend data for a specific platform and category."""
    topics = MOCK_TOPICS_BY_CATEGORY.get(category, DEFAULT_TOPICS)
    # Shuffle and take a subset to simulate variety
    shuffled = topics.copy()
    random.shuffle(shuffled)
    selected = shuffled[:6]

    platform_pulses = {
        "twitter":   f"{category} conversations are exploding on Twitter/X with high engagement on short-form takes and threads.",
        "reddit":    f"Reddit communities are deep-diving into {category} with long-form discussions and AMAs gaining traction.",
        "linkedin":  f"LinkedIn professionals are sharing {category} insights with strong B2B engagement on thought leadership posts.",
        "instagram": f"Instagram creators are building {category} audiences through visual storytelling and carousel content.",
        "medium":    f"Medium writers are publishing in-depth {category} analysis with strong reader retention metrics.",
        "quora":     f"Quora is seeing high-quality {category} Q&A with expert answers gaining significant upvotes.",
    }

    best_formats = {
        "twitter":   "Thread (5-10 tweets)",
        "reddit":    "Long-form discussion post",
        "linkedin":  "Carousel or article",
        "instagram": "Carousel or Reel",
        "medium":    "Long-form article",
        "quora":     "Detailed answer with examples",
    }

    hashtag_sets = {
        "twitter":   [f"#{category}", "#AI", "#Tech", "#Innovation", "#BuildInPublic"],
        "reddit":    [],
        "linkedin":  [f"#{category}", "#Innovation", "#Leadership", "#FutureOfWork", "#Technology"],
        "instagram": [f"#{category}", "#Tech", "#Innovation", "#Creator", "#Digital", "#Future"],
        "medium":    [f"#{category}", "technology", "innovation", "future", "ai"],
        "quora":     [],
    }

    return {
        "platform": platform,
        "category": category,
        "trending_topics": selected,
        "trending_hashtags": hashtag_sets.get(platform, []),
        "platform_pulse": platform_pulses.get(platform, f"{category} is trending on {platform}."),
        "best_content_format": best_formats.get(platform, "Post"),
        "peak_engagement_window": "9-11am or 6-8pm local time",
        "trend_velocity": random.randint(55, 95),
    }


def get_mock_trend_insights(category: str, platforms: list) -> dict:
    """Return mock cross-platform trend insights."""
    momentum_pct = random.randint(18, 67)
    return {
        "global_trend_score": random.randint(62, 91),
        "trend_headline": f"{category} conversations are surging {momentum_pct}% across platforms this week",
        "momentum_direction": random.choice(["Accelerating", "Accelerating", "Steady"]),
        "momentum_change_pct": momentum_pct,
        "ai_insights": [
            f"{category} content is performing 2-3x better on LinkedIn compared to last month — professionals are hungry for practical insights.",
            f"Twitter/X threads about {category} are getting significantly more engagement than single tweets — format matters.",
            f"Reddit discussions around {category} are becoming more technical and nuanced, signaling a maturing audience.",
        ],
        "content_opportunities": [
            {"platform": "linkedin", "format": "Carousel", "topic": f"5 {category} trends reshaping the industry", "angle": "Data-driven with actionable takeaways", "urgency": "Post Now"},
            {"platform": "twitter", "format": "Thread", "topic": f"Unpopular opinions about {category}", "angle": "Contrarian take that sparks debate", "urgency": "Post Now"},
            {"platform": "reddit", "format": "Long-form post", "topic": f"Deep dive: What's actually happening with {category}", "angle": "Honest, nuanced analysis without hype", "urgency": "This Week"},
        ],
        "hottest_platform": random.choice(["linkedin", "twitter", "reddit"]),
        "emerging_angle": f"The intersection of {category} and creator economy is an underexplored angle gaining early traction.",
    }


def get_mock_full_trend_analysis(category: str, platforms: list, search_query: str = None) -> dict:
    """Return complete mock trend analysis response."""
    platform_data = {p: get_mock_platform_trends(p, category) for p in platforms}
    insights = get_mock_trend_insights(category, platforms)
    return {
        "category": category,
        "search_query": search_query,
        "platforms": platform_data,
        "insights": insights,
        "fetched_at": datetime.utcnow().isoformat(),
        "_mock": True,
    }
