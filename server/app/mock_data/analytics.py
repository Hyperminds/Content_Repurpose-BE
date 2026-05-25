"""
Mock analytics data for development mode.
Returns realistic analytics without any real data.
"""

import random
from datetime import datetime, timezone, timedelta


def get_mock_publishing_stats() -> dict:
    return {
        "posted": random.randint(18, 45),
        "scheduled": random.randint(3, 12),
        "failed": random.randint(0, 3),
        "pending_manual": random.randint(1, 6),
        "success_rate": random.randint(88, 98),
        "_mock": True,
    }


def get_mock_platform_performance() -> list:
    platforms = ["linkedin", "twitter", "instagram", "reddit", "medium"]
    result = []
    for p in platforms:
        total = random.randint(8, 25)
        posted = random.randint(int(total * 0.7), total)
        result.append({
            "platform": p,
            "total": total,
            "posted": posted,
            "failed": total - posted,
            "success_rate": round((posted / total) * 100),
        })
    return result


def get_mock_posting_timeline(days: int = 14) -> list:
    result = []
    base = datetime.now(timezone.utc)
    for i in range(days):
        day = base - timedelta(days=days - i)
        result.append({
            "date": day.strftime("%Y-%m-%d"),
            "count": random.randint(0, 5),
            "platforms": random.sample(["linkedin", "twitter", "instagram"], k=random.randint(1, 3)),
        })
    return result


def get_mock_best_posting_times() -> dict:
    return {
        "linkedin":  {"time": "9:00 AM", "day": "Tuesday", "score": 92},
        "twitter":   {"time": "12:00 PM", "day": "Wednesday", "score": 88},
        "instagram": {"time": "6:00 PM", "day": "Friday", "score": 85},
        "reddit":    {"time": "10:00 AM", "day": "Monday", "score": 79},
        "medium":    {"time": "8:00 AM", "day": "Thursday", "score": 74},
    }


def get_mock_ai_usage_summary() -> dict:
    return {
        "total_tokens": random.randint(45000, 180000),
        "total_cost": round(random.uniform(0.8, 4.5), 4),
        "total_generations": random.randint(25, 80),
        "avg_tokens_per_generation": random.randint(800, 2200),
        "_mock": True,
    }


def get_mock_ai_platform_breakdown() -> list:
    platforms = ["linkedin", "twitter", "instagram", "reddit", "medium", "quora"]
    return [
        {
            "platform": p,
            "total_tokens": random.randint(5000, 35000),
            "generations": random.randint(4, 18),
            "avg_tokens": random.randint(600, 2000),
        }
        for p in platforms
    ]


def get_mock_ai_efficiency() -> dict:
    labels = ["Excellent", "Good", "Average"]
    colors = ["success", "success", "warning"]
    idx = random.randint(0, 2)
    return {
        "efficiency_label": labels[idx],
        "efficiency_color": colors[idx],
        "avg_tokens_per_generation": random.randint(900, 1800),
        "most_expensive_platform": random.choice(["linkedin", "medium", "reddit"]),
        "most_efficient_platform": random.choice(["twitter", "instagram"]),
        "_mock": True,
    }


def get_mock_content_score(content: str, platform: str) -> dict:
    score = random.randint(58, 92)
    return {
        "content_score": score,
        "hook_strength": random.randint(50, 95),
        "readability": random.randint(60, 95),
        "cta_effectiveness": random.randint(40, 88),
        "platform_compatibility": random.randint(55, 95),
        "viral_potential": random.choice(["High", "Medium", "Medium", "Low"]),
        "suggestions": [
            {"icon": "💡", "text": "Consider opening with a stronger hook — a question or bold statement performs best."},
            {"icon": "📊", "text": "Add specific data points or statistics to increase credibility."},
            {"icon": "🎯", "text": "Your CTA could be more specific — tell readers exactly what action to take."},
        ],
        "_mock": True,
    }
