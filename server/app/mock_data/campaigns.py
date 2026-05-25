"""
Mock campaign data for development mode.
Returns realistic campaign strategies and content without AI calls.
"""

import random

MOCK_STRATEGIES = [
    {
        "campaign_name": "Q3 Thought Leadership Push",
        "objective": "Establish authority in the AI content space",
        "target_audience": "Marketing professionals and startup founders",
        "platforms": ["linkedin", "twitter", "medium"],
        "duration_weeks": 4,
        "content_pillars": ["AI tools education", "Industry insights", "Personal stories", "Case studies"],
        "weekly_themes": [
            {"week": 1, "theme": "The AI Content Revolution", "focus": "Educational content about AI in marketing"},
            {"week": 2, "theme": "Real Results", "focus": "Case studies and data-driven posts"},
            {"week": 3, "theme": "Tools & Tactics", "focus": "Practical how-to content"},
            {"week": 4, "theme": "Future Vision", "focus": "Thought leadership and predictions"},
        ],
        "kpis": ["Profile views", "Follower growth", "Content engagement rate", "DM inquiries"],
        "posting_frequency": {"linkedin": "4x/week", "twitter": "daily", "medium": "1x/week"},
    }
]

MOCK_DAY_CONTENT = {
    "linkedin": """The biggest mistake I see content creators make in 2025:

They create content FOR their audience instead of WITH their audience.

Here's what I mean:

Most creators sit down, think "what should I post today?" and write something they think people want to hear.

The best creators ask their audience directly:
→ What's your biggest challenge right now?
→ What would you pay to solve?
→ What content have you found most useful?

Then they create content that answers those exact questions.

The result? Content that feels like it was written specifically for each reader.

Because it was.

What's the last piece of content you created based on direct audience feedback?

#ContentMarketing #ContentStrategy #CreatorEconomy #Marketing #LinkedIn""",

    "twitter": "The best content strategy is the one you'll actually stick to. Consistency beats perfection every time. #ContentMarketing",

    "instagram": """Behind the scenes of building a content system that actually works 📱

Most people overcomplicate content creation. Here's my simple framework:

1️⃣ One core idea per week
2️⃣ Repurpose across 3 platforms
3️⃣ Engage for 30 min daily
4️⃣ Review analytics every Sunday

That's it. No complicated tools. No 50-step process.

Simple systems win.

Save this for when you're feeling overwhelmed by content creation 🔖

#ContentCreator #ContentStrategy #SocialMedia #Marketing #CreatorTips #ContentMarketing #DigitalMarketing #Entrepreneur #BuildInPublic""",
}

MOCK_CAMPAIGN_ANALYTICS = {
    "total_posts": random.randint(12, 28),
    "published_posts": random.randint(8, 20),
    "scheduled_posts": random.randint(2, 8),
    "engagement_rate": round(random.uniform(3.2, 8.7), 1),
    "reach_growth": f"+{random.randint(12, 45)}%",
    "top_performing_platform": random.choice(["linkedin", "twitter"]),
    "content_quality_avg": random.randint(72, 91),
    "recommendations": [
        "Increase LinkedIn posting frequency — your audience is most active there",
        "Twitter threads are outperforming single tweets 3:1 — lean into threads",
        "Medium articles are driving the most profile visits — publish more long-form",
    ],
}


def get_mock_campaign_strategy(campaign_data: dict) -> dict:
    strategy = random.choice(MOCK_STRATEGIES).copy()
    strategy["campaign_name"] = campaign_data.get("name", strategy["campaign_name"])
    strategy["_mock"] = True
    return strategy


def get_mock_day_content(platform: str, topic: str = "") -> str:
    return MOCK_DAY_CONTENT.get(platform, MOCK_DAY_CONTENT["linkedin"])


def get_mock_campaign_analytics(campaign_id: str) -> dict:
    result = MOCK_CAMPAIGN_ANALYTICS.copy()
    result["campaign_id"] = campaign_id
    result["_mock"] = True
    return result
