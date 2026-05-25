"""
Mock social presence analysis data for development mode.
Returns realistic profile scores and recommendations without AI calls.
"""

import random

PLATFORM_STRENGTHS = {
    "linkedin": ["Strong professional headline", "Consistent posting schedule", "Good use of industry keywords", "Active engagement with comments"],
    "twitter":  ["Punchy bio with clear value prop", "Strong hook writing style", "Good hashtag strategy", "Consistent brand voice"],
    "instagram":["Cohesive visual aesthetic", "Strong caption hooks", "Good use of Stories", "Consistent posting frequency"],
    "reddit":   ["Authentic community engagement", "High-value long-form content", "Good subreddit targeting", "Strong upvote ratio"],
    "medium":   ["Well-structured articles", "Strong headline writing", "Good SEO optimization", "Consistent publication schedule"],
    "quora":    ["Expert-level answer quality", "Strong credentials display", "Good topic expertise", "High upvote rate"],
}

PLATFORM_WEAKNESSES = {
    "linkedin": ["Banner image missing or generic", "Summary section too short", "Not using featured section", "Inconsistent posting frequency"],
    "twitter":  ["Bio lacks clear CTA", "Not using pinned tweet effectively", "Hashtag overuse diluting reach", "Engagement with others is low"],
    "instagram":["Bio CTA is weak", "Not using Reels enough", "Hashtag strategy needs refinement", "Stories engagement could improve"],
    "reddit":   ["Self-promotion ratio too high", "Not engaging enough in comments", "Post titles could be more compelling", "Missing niche subreddit opportunities"],
    "medium":   ["Articles lack internal linking", "Not submitting to publications", "SEO tags underutilized", "Inconsistent publishing schedule"],
    "quora":    ["Profile bio incomplete", "Not answering trending questions", "Missing credentials in answers", "Response time could be faster"],
}

PLATFORM_RECOMMENDATIONS = {
    "linkedin": [
        {"priority": "high", "category": "Banner", "title": "Add a professional banner image", "description": "Your banner is the first thing visitors see. Add a branded banner that communicates your value proposition clearly.", "impact": "Can increase profile views by 40%"},
        {"priority": "medium", "category": "Content", "title": "Increase posting to 3x per week", "description": "LinkedIn's algorithm rewards consistency. Aim for 3 posts per week mixing insights, stories, and industry commentary.", "impact": "Significantly improves organic reach"},
        {"priority": "low", "category": "Hashtags", "title": "Optimize hashtag strategy", "description": "Use 3-5 highly relevant hashtags per post. Mix high-volume (#AI) with niche (#AIStartups) for best reach.", "impact": "Improves content discoverability"},
    ],
    "twitter": [
        {"priority": "high", "category": "Bio", "title": "Rewrite bio with clear value prop", "description": "Your bio has 160 characters to explain who you are and why people should follow you. Make every word count.", "impact": "Directly impacts follow conversion rate"},
        {"priority": "medium", "category": "Content", "title": "Start writing threads", "description": "Threads get 3-5x more engagement than single tweets. Share your expertise in thread format weekly.", "impact": "Major engagement boost"},
        {"priority": "low", "category": "Engagement", "title": "Reply to 5 accounts daily", "description": "Consistent engagement with others in your niche builds visibility and community.", "impact": "Grows follower count organically"},
    ],
    "instagram": [
        {"priority": "high", "category": "Reels", "title": "Post 3 Reels per week", "description": "Instagram is heavily pushing Reels in the algorithm. Short-form video is the fastest way to grow right now.", "impact": "Highest reach potential on the platform"},
        {"priority": "medium", "category": "Bio", "title": "Add a link-in-bio tool", "description": "Use a link-in-bio service to drive traffic to multiple destinations from your single bio link.", "impact": "Improves conversion from profile visits"},
        {"priority": "low", "category": "Hashtags", "title": "Use 10-15 targeted hashtags", "description": "Research niche hashtags with 10K-500K posts for best discoverability without getting lost.", "impact": "Improves content reach"},
    ],
    "reddit": [
        {"priority": "high", "category": "Community", "title": "Increase comment engagement", "description": "Reddit rewards users who contribute to discussions. Aim to leave 5 thoughtful comments per day before posting.", "impact": "Builds karma and community trust"},
        {"priority": "medium", "category": "Content", "title": "Improve post title quality", "description": "Your title is everything on Reddit. Study top posts in your target subreddits and model your titles accordingly.", "impact": "Directly impacts upvote rate"},
        {"priority": "low", "category": "Strategy", "title": "Expand to 3 new subreddits", "description": "Identify adjacent subreddits where your content would be valuable and start contributing.", "impact": "Expands audience reach"},
    ],
    "medium": [
        {"priority": "high", "category": "Distribution", "title": "Submit to 3 publications", "description": "Medium publications have built-in audiences. Getting accepted to relevant publications can 10x your article reach.", "impact": "Massive reach multiplier"},
        {"priority": "medium", "category": "SEO", "title": "Optimize article tags", "description": "Use all 5 available tags and research which tags have the most followers on Medium.", "impact": "Improves organic discovery"},
        {"priority": "low", "category": "Consistency", "title": "Publish on a fixed schedule", "description": "Readers follow writers who publish consistently. Pick a day and stick to it.", "impact": "Builds loyal readership"},
    ],
    "quora": [
        {"priority": "high", "category": "Profile", "title": "Complete your credentials section", "description": "Quora readers trust answers more when they can see your expertise. Add specific credentials for each topic you answer.", "impact": "Significantly increases answer credibility"},
        {"priority": "medium", "category": "Strategy", "title": "Answer trending questions first", "description": "Find questions with high view counts but few answers. Being early on trending questions maximizes visibility.", "impact": "Exponential reach on viral questions"},
        {"priority": "low", "category": "Content", "title": "Add images to your answers", "description": "Answers with relevant images get significantly more upvotes and shares.", "impact": "Improves engagement rate"},
    ],
}


def get_mock_platform_analysis(platform: str, profile_data: dict) -> dict:
    """Return mock analysis for a single platform."""
    score = random.randint(52, 88)
    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"

    strengths = random.sample(PLATFORM_STRENGTHS.get(platform, PLATFORM_STRENGTHS["linkedin"]), 3)
    weaknesses = random.sample(PLATFORM_WEAKNESSES.get(platform, PLATFORM_WEAKNESSES["linkedin"]), 3)
    recommendations = PLATFORM_RECOMMENDATIONS.get(platform, PLATFORM_RECOMMENDATIONS["linkedin"])

    return {
        "platform": platform,
        "score": score,
        "grade": grade,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "growth_opportunities": [
            f"Leverage {platform} analytics to identify your best-performing content types",
            f"Cross-promote your {platform} content to your other platforms for amplified reach",
        ],
        "recommendations": recommendations,
        "profile_completeness": random.randint(45, 90),
        "posting_consistency": random.randint(30, 85),
        "branding_quality": random.randint(50, 88),
        "content_quality": random.randint(55, 90),
        "cta_effectiveness": random.randint(35, 80),
        "presence_level": random.choice(["Developing", "Established", "Growing"]),
        "_mock": True,
    }


def get_mock_overall_analysis(platform_results: list, platforms: list) -> dict:
    """Return mock overall social presence analysis."""
    scores = [r.get("score", 60) for r in platform_results]
    overall = round(sum(scores) / len(scores)) if scores else 65
    grade = "A" if overall >= 85 else "B" if overall >= 70 else "C" if overall >= 55 else "D"

    return {
        "overall_score": overall,
        "overall_grade": grade,
        "presence_level": "Developing" if overall < 60 else "Established" if overall < 75 else "Authority",
        "summary": f"Your social presence shows strong potential with an overall score of {overall}/100. Focus on consistency and content quality to accelerate growth.",
        "top_priority": "Establish a consistent posting schedule across all platforms before optimizing individual elements.",
        "cross_platform_insights": [
            "Your LinkedIn and Twitter audiences likely overlap significantly — repurpose content between them.",
            "Your strongest platform should be used to drive traffic to your weaker ones.",
            "Consistent branding across all platforms will compound your authority over time.",
        ],
        "improvement_roadmap": [
            {"week": "Week 1-2", "focus": "Profile optimization", "action": "Complete all profile sections, add professional photos, write compelling bios"},
            {"week": "Week 3-4", "focus": "Content consistency", "action": "Establish a posting schedule and create a 2-week content calendar"},
            {"week": "Month 2", "focus": "Engagement growth", "action": "Actively engage with others in your niche daily to build community"},
        ],
        "_mock": True,
    }


def get_mock_full_analysis(profiles_data: dict) -> dict:
    """Return complete mock social presence analysis."""
    platforms = list(profiles_data.keys())
    platform_analyses = [get_mock_platform_analysis(p, profiles_data[p]) for p in platforms]
    overall = get_mock_overall_analysis(platform_analyses, platforms)

    return {
        "platform_analyses": platform_analyses,
        "overall": overall,
        "analyzed_platforms": platforms,
        "_mock": True,
    }
