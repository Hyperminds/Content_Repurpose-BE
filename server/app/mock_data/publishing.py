"""
Mock publishing data for development mode.
Simulates platform posting behavior without calling any real platform APIs.
Rotates outcomes dynamically to simulate realistic production behavior.
"""

import random
import string
from datetime import datetime, timezone


# Weighted outcome pool — simulates realistic success/failure ratios
_OUTCOME_POOL = [
    "posted", "posted", "posted", "posted", "posted",   # 71% success
    "posted", "posted",
    "failed",                                             # 14% failure
    "pending_manual",                                     # 14% manual
]

_FAILURE_REASONS = [
    "Rate limit exceeded — retry in 15 minutes",
    "Authentication token expired — please reconnect your account",
    "Content policy violation detected — review and resubmit",
    "Platform API temporarily unavailable",
    "Media upload failed — unsupported format",
    "Account posting limit reached for today",
]

_PLATFORM_POST_ID_PREFIXES = {
    "linkedin": "urn:li:share:",
    "twitter":  "tweet_",
    "instagram":"ig_media_",
    "reddit":   "t3_",
    "medium":   "medium_post_",
    "meta":     "fb_post_",
    "quora":    "quora_ans_",
}


def _random_id(length: int = 12) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def get_mock_publish_result(platform: str, simulate_failure: bool = False, simulate_rate_limit: bool = False) -> dict:
    """
    Simulate a platform publish call.
    Returns the same structure as real adapter publish_post().
    """
    if simulate_rate_limit:
        return {
            "success": False,
            "error": f"[MOCK] Rate limit exceeded for {platform}. Retry after 60 seconds.",
            "error_code": 429,
        }

    if simulate_failure:
        return {
            "success": False,
            "error": f"[MOCK] {random.choice(_FAILURE_REASONS)}",
            "error_code": random.choice([400, 401, 403, 500, 503]),
        }

    outcome = random.choice(_OUTCOME_POOL)

    if outcome == "failed":
        return {
            "success": False,
            "error": f"[MOCK] {random.choice(_FAILURE_REASONS)}",
            "error_code": random.choice([400, 500, 503]),
        }

    prefix = _PLATFORM_POST_ID_PREFIXES.get(platform, "post_")
    post_id = f"{prefix}{_random_id()}"

    return {
        "success": True,
        "platform_post_id": post_id,
        "mock": True,
        "simulated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_mock_scheduled_posts(user_id: str, count: int = 5) -> list:
    """Generate mock scheduled posts for the queue."""
    platforms = ["linkedin", "twitter", "instagram", "reddit", "medium"]
    previews = [
        "The future of AI content creation is here — and it's changing everything",
        "3 things I learned building a SaaS product from scratch",
        "Why most developers underestimate the power of documentation",
        "The one metric that actually predicts startup success",
        "How to build an audience without spending a dollar on ads",
    ]
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    posts = []
    for i in range(count):
        scheduled_at = now + timedelta(hours=random.randint(1, 72))
        posts.append({
            "id": f"mock_sched_{_random_id(8)}",
            "unique_post_id": f"REP-{platforms[i % len(platforms)][:4].upper()}-MOCK-{i+1:04d}",
            "platform": platforms[i % len(platforms)],
            "content_preview": previews[i % len(previews)],
            "status": "scheduled",
            "scheduled_at": scheduled_at.isoformat(),
            "publish_type": "scheduled",
            "_mock": True,
        })
    return posts
